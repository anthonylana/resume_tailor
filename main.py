import os

# Some software sets SSLKEYLOGFILE to an unwritable path, which breaks all HTTPS calls.
os.environ.pop("SSLKEYLOGFILE", None)

import difflib
import sys
from pathlib import Path

from dotenv import load_dotenv
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters

from crew_setup import (build_llm, build_tailor_crew, revise_resume,
                        to_tailored, to_verification, to_verification_json)
from skill_memory import format_for_prompt, load_memory, record_verification

BASE_DIR = Path(__file__).resolve().parent
JOB_POSTING_FILE = BASE_DIR / "job_posting.txt"
load_dotenv(BASE_DIR / ".env")


def ask(prompt: str) -> str:
    val = ""
    while not val.strip():
        val = input(f"{prompt}\n> ")
    return val.strip()


def ask_multiline(prompt: str) -> str:
    print(f"{prompt}\n(paste text, then type END on its own line)")
    lines = []
    while (line := input()) != "END":
        lines.append(line)
    return "\n".join(lines)


def read_job_posting_file() -> str:
    """Read the job posting from resume_tailor/job_posting.txt.

    Creates the file if missing, and waits for the user to paste content
    if it is empty.
    """
    if not JOB_POSTING_FILE.exists():
        JOB_POSTING_FILE.write_text("", encoding="utf-8")
        print(f"\nCreated empty file: {JOB_POSTING_FILE}")

    while True:
        text = JOB_POSTING_FILE.read_text(encoding="utf-8").strip()
        if text:
            preview = text[:300].replace("\n", " ")
            print(f"\nLoaded job_posting.txt ({len(text)} characters)")
            print(f"Preview: {preview}{'...' if len(text) > 300 else ''}")
            if ask("Is this the right job posting? (y/n)").lower().startswith("y"):
                return text
            print(f"Update {JOB_POSTING_FILE.name}, save it, then continue.")
        else:
            print(f"\n{JOB_POSTING_FILE} is empty.")
            print("Paste the job posting into it and SAVE the file.")

        input("Press Enter when the file is ready...")


def get_job_source() -> str:
    print("\nWhere is the job posting?")
    print("  1) Google Doc URL/ID")
    print("  2) Local text file (any path)")
    print("  3) Paste it here")
    print(f"  4) Use {JOB_POSTING_FILE.name} in the project folder (default)")
    choice = input("Choose 1, 2, 3 or 4 [4]\n> ").strip() or "4"

    if choice == "1":
        return ask("Job posting Google Doc URL or ID")
    if choice == "2":
        return Path(ask("Path to the text file")).read_text(encoding="utf-8")
    if choice == "3":
        return ask_multiline("Paste the job posting")
    return read_job_posting_file()


def get_tool(tools, name: str):
    return next(t for t in tools if t.name == name)


def create_resume_doc(create_tool, title: str, content: str) -> str:
    """Create the doc and fail loudly if the MCP tool returned an error string."""
    result = str(create_tool.run(title=title, content=content)).strip()
    if "docs.google.com/document/d/" not in result:
        raise RuntimeError(f"Google Doc creation failed:\n{result}")
    return result


def show_review(original: str, tailored) -> None:
    print("\n" + "#" * 70 + "\nPROPOSED CHANGES\n" + "#" * 70)
    for i, c in enumerate(tailored.changes, 1):
        print(f"{i}. {c}")

    print("\n" + "#" * 70 + "\nDIFF (- original / + tailored)\n" + "#" * 70)
    orig_lines = original.split("\n", 2)[-1].splitlines()  # drop 'TITLE:' header
    for line in difflib.unified_diff(orig_lines, tailored.resume_markdown.splitlines(),
                                     "original", "tailored", lineterm="", n=1):
        print(line)

    print("\n" + "#" * 70 + "\nFULL TAILORED RESUME\n" + "#" * 70)
    print(tailored.resume_markdown)


def main() -> None:
    if not os.getenv("MAMMOUTH_API_KEY"):
        sys.exit("MAMMOUTH_API_KEY missing in .env")

    resume_doc = ask("Resume Google Doc URL or ID (it will NOT be modified)")
    job_source = get_job_source()

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(BASE_DIR / "gdocs_mcp_server.py")],
        env={**os.environ},
    )

    with MCPServerAdapter(server_params) as mcp_tools:
        print("MCP tools available:", [t.name for t in mcp_tools])
        read_tool = get_tool(mcp_tools, "read_google_doc")
        create_tool = get_tool(mcp_tools, "create_google_doc")

        original_text = read_tool.run(document=resume_doc)

        llm = build_llm()
        crew, verify_task = build_tailor_crew(llm, read_tool)

        print("\nAnalyzing the job posting and your resume... "
              "the agent will ask you questions one at a time.\n")
        memory = load_memory()
        print(f"Loaded {len(memory['skills'])} remembered skill(s) from skills_memory.json")

        result = crew.kickoff(inputs={
            "resume_doc": resume_doc,
            "job_source": job_source,
            "skill_memory": format_for_prompt(memory),
        })

        try:
            tailored = to_tailored(result)
        except ValueError as e:
            print(f"\nCould not get a resume from the writer: {e}")
            print("Raw output was:\n", getattr(result, "raw", result))
            return

        verification_json = to_verification_json(verify_task.output)
        verification = to_verification(verify_task.output)
        if verification:
            added, updated = record_verification(verification)
            print(f"Skill memory: {added} new, {updated} updated -> skills_memory.json")
        else:
            print("Warning: could not parse skill verification; memory not updated.")


        print("\nDraft ready. Review it below.")

        while True:
            show_review(original_text, tailored)
            choice = ask("\n[a]pprove and create new doc / [r]evise with feedback / [q]uit").lower()
            if choice.startswith("q"):
                print("Aborted. Nothing was created.")
                return
            if choice.startswith("r"):
                feedback = ask_multiline("What should be changed?")
                try:
                    tailored = revise_resume(llm, read_tool, tailored, verification_json, feedback)
                except ValueError as e:
                    print(f"Revision failed ({e}); keeping the previous draft.")
                continue
            if choice.startswith("a"):
                break

        original_title = original_text.splitlines()[0].removeprefix("TITLE:").strip()
        default_title = f"{original_title} - Tailored" if original_title else "Tailored Resume"
        title = input(f"Name of the NEW Google Doc [{default_title}]\n> ").strip() or default_title

        if not ask(f"Create new doc '{title}'? (y/n)").lower().startswith("y"):
            print("Cancelled. Nothing was created.")
            return

        try:
            url = create_resume_doc(create_tool, title, tailored.resume_markdown)
        except RuntimeError as e:
            print(f"\n{e}")
            print("\nIf you see 'insufficient authentication scopes' or 403, "
                  "fix the scopes in google_auth.py and delete token.json (see below).")
            fallback = BASE_DIR / "tailored_resume.md"
            fallback.write_text(tailored.resume_markdown, encoding="utf-8")
            print(f"Your tailored resume was saved locally to: {fallback}")
            return

        print(f"\nNew resume created: {url}")
        print("Your original resume was not modified.")


if __name__ == "__main__":
    main()