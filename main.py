import os
import sys
import argparse
import config
from graph import create_agent_graph


def main():
    parser = argparse.ArgumentParser(description="Win-Back Campaign Workflow Agent")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of active leads processed (for testing)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Win-Back Sales Campaign — Workflow Agent")
    print("=" * 60)

    # 1. Validate environment configuration before running
    config.validate()
    
    # 2. Bind the runtime limit parameter to environment to avoid module-level mutation
    if args.limit is not None:
        os.environ['WINBACK_LIMIT'] = str(args.limit)

    print("[Main] Compiling the agent workflow graph...\n")
    graph = create_agent_graph()
    
    print("[Main] Invoking the pipeline...\n")
    
    # 3. Invoke the LangGraph pipeline with the initial state
    result = graph.invoke({
        "leads_to_process": [],
        "current_lead": None,
        "processed_leads": [],
        "final_report": None
    })
    
    # 4. Safely print the final run summary report
    print("\n" + "=" * 60)
    print("  FINAL EXECUTION REPORT")
    print("=" * 60)
    safe_report = result.get("final_report", "No report generated.").encode(
        sys.stdout.encoding or 'utf-8', errors='replace'
    ).decode(sys.stdout.encoding or 'utf-8')
    safe_report = safe_report.replace('\r\n', '\n').replace('\r', '')
    print(safe_report)
    print("=" * 60)
    
    print("\n[Success] Campaign pipeline finished execution successfully!")


if __name__ == "__main__":
    main()
