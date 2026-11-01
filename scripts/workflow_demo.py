"""Submit concurrent six-agent education workflows and print their live DAG state."""
import argparse
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflows", type=int, default=3, choices=range(1, 21))
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env.cloud")
    keys = json.loads(os.environ["API_KEYS"])
    key = next(iter(keys.values()))
    headers = {"Authorization": "Bearer " + key}
    examples = [
        ("Cloud computing in education", "Computer Science"),
        ("Ethical use of generative AI in university assessment", "Education"),
        ("How distributed systems recover from worker failure", "Cloud Computing"),
        ("Privacy considerations in online learning platforms", "Cybersecurity"),
    ]

    def submit(index):
        title, subject = examples[index % len(examples)]
        payload = {
            "assignment_title": title,
            "assignment_description": f"Write a structured academic report about {title.lower()} with verified sources.",
            "subject": subject,
            "deadline": "7 days",
            "student_draft": f"This draft introduces {title.lower()} and will be expanded with cited evidence.",
            "citation_style": "APA",
        }
        with httpx.Client(base_url=args.url, headers=headers, timeout=15) as client:
            response = client.post("/api/v1/tasks", headers={"Idempotency-Key": uuid.uuid4().hex},
                                   json={"agent": "assignment-success-team", "payload": payload,
                                         "conversation_id": "concurrent-workflow-demo"})
            response.raise_for_status()
            return response.json()

    with ThreadPoolExecutor(max_workers=args.workflows) as pool:
        finals = list(pool.map(submit, range(args.workflows)))
    print(f"Submitted {args.workflows} workflows ({args.workflows * 6} durable stage jobs).")
    deadline = time.monotonic() + args.timeout
    last_line = None
    with httpx.Client(base_url=args.url, headers=headers, timeout=15) as client:
        while time.monotonic() < deadline:
            workflows = [client.get("/api/v1/workflows/" + item["workflow_id"]).json() for item in finals]
            counts = {}
            for workflow in workflows:
                for stage in workflow:
                    counts[stage["status"]] = counts.get(stage["status"], 0) + 1
            line = " | ".join(f"{name}={count}" for name, count in sorted(counts.items()))
            if line != last_line:
                print(line)
                last_line = line
            terminal = {"succeeded", "dead", "cancelled"}
            if all(item["status"] in terminal for workflow in workflows for item in workflow):
                break
            time.sleep(1)
        else:
            raise SystemExit("Timed out waiting for the workflows")

    succeeded = sum(item["status"] == "succeeded" for workflow in workflows for item in workflow)
    print(f"Completed {succeeded}/{args.workflows * 6} stage jobs successfully.")
    for index, workflow in enumerate(workflows, 1):
        path = " -> ".join(f'{stage["stage"]}:{stage["status"]}' for stage in workflow)
        print(f"Workflow {index}: {path}")


if __name__ == "__main__":
    main()
