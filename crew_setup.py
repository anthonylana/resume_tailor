import json
import os
import re
from typing import Literal

from crewai import LLM, Agent, Crew, Process, Task
from pydantic import BaseModel, Field, ValidationError

from human_tools import AskHumanTool


# ---------- Structured outputs ----------
class SkillEvidence(BaseModel):
    skill: str
    status: Literal["in_resume", "confirmed_by_user", "denied_by_user"]
    evidence: str = Field(description="Where/how it was used (resume quote or user answer)")


class SkillVerification(BaseModel):
    job_title: str
    company: str
    key_requirements: list[str]
    skills: list[SkillEvidence]


class TailoredResume(BaseModel):
    resume_markdown: str = Field(description="Full resume, '# ' headings, '- ' bullets")
    changes: list[str] = Field(description="Human-readable list of every change made")


def _extract_json(text: str) -> dict | None:
    """Find the first {...} block in text (handles ```json fences)."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def to_tailored(output) -> TailoredResume:
    """Turn a CrewOutput/TaskOutput into a TailoredResume, whatever the model returned."""
    if output is None:
        raise ValueError("Writer produced no output.")

    parsed = getattr(output, "pydantic", None)
    if isinstance(parsed, TailoredResume):
        return parsed

    raw = (getattr(output, "raw", "") or "").strip()
    data = _extract_json(raw)
    if data and "resume_markdown" in data:
        try:
            return TailoredResume.model_validate(data)
        except ValidationError:
            pass

    # Last resort: the model returned the resume as plain markdown
    text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", raw).strip()
    if not text:
        raise ValueError("Writer returned an empty answer.")
    return TailoredResume(
        resume_markdown=text,
        changes=["(Model returned plain text, so no change list is available.)"],
    )


def to_verification_json(task_output) -> str:
    """Serialize the verification result even if pydantic parsing failed."""
    parsed = getattr(task_output, "pydantic", None)
    if parsed is not None:
        return parsed.model_dump_json(indent=2)
    return (getattr(task_output, "raw", "") or "No verification data.").strip()


def to_verification(task_output) -> SkillVerification | None:
    """Get a SkillVerification object, or None if the model output is unusable."""
    parsed = getattr(task_output, "pydantic", None)
    if isinstance(parsed, SkillVerification):
        return parsed
    data = _extract_json(getattr(task_output, "raw", "") or "")
    if data:
        try:
            return SkillVerification.model_validate(data)
        except ValidationError:
            return None
    return None


# ---------- LLM ----------
def build_llm() -> LLM:
    return LLM(
        model=str(os.getenv("MAMMOUTH_MODEL")),
        base_url="https://api.mammouth.ai/v1",
        api_key=os.getenv("MAMMOUTH_API_KEY"),
        temperature=0.1
    )


TRUTH_RULES = """
STRICT HONESTY RULES:
- NEVER invent skills, technologies, employers, dates, titles, metrics or degrees.
- Only use facts from the original resume OR facts the candidate confirmed via ask_human.
- Skills marked 'denied_by_user' must NOT appear anywhere.
- You MAY reorder, rephrase, emphasize, and remove less relevant content.
- Keep job titles, company names and dates exactly as in the original.
"""

OUTPUT_RULES = """
FINAL ANSWER RULES (very important):
- Do NOT ask the candidate to approve or review the resume. Never put the resume in a question.
- Your FINAL ANSWER must be ONLY a JSON object, no extra text:
  {"resume_markdown": "<full resume, with \\n for new lines>", "changes": ["change 1", "change 2"]}
"""

def _writer_agent(llm, read_tool, allow_questions: bool = False) -> Agent:
    tools = [read_tool]
    if allow_questions:
        tools.append(AskHumanTool())
    return Agent(
        role="Resume Writer",
        goal="Tailor the resume to the job while staying 100% truthful.",
        backstory=("Senior tech recruiter and resume writer who never fabricates. "
                   "You never ask for approval: the application handles review."),
        tools=tools,
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )


def build_tailor_crew(llm: LLM, read_tool) -> tuple[Crew, Task]:
    human = AskHumanTool()

    analyst = Agent(
        role="Job Posting Analyst",
        goal="Extract the precise requirements of a job posting.",
        backstory="Technical recruiter expert at decoding job descriptions.",
        tools=[read_tool],
        llm=llm, verbose=False, allow_delegation=False,
    )
    verifier = Agent(
        role="Skills Verifier",
        goal="Determine with certainty which required skills the candidate has.",
        backstory=("Meticulous fact-checker. You never assume a skill. "
                   "If it's not explicitly in the resume, you ask the candidate."),
        tools=[read_tool, human],
        llm=llm, verbose=False, allow_delegation=False,
    )
    writer = _writer_agent(llm, read_tool)

    analyze_task = Task(
        description=(
            "Job posting source: {job_source}\n\n"
            "If the source looks like a Google Doc URL or ID, read it with the "
            "read_google_doc tool. Otherwise the source IS the posting text.\n"
            "Extract: job title, company, required skills/technologies, "
            "nice-to-have skills, responsibilities, important keywords."
        ),
        expected_output="A structured list of the job requirements and keywords.",
        agent=analyst,
    )

    verify_task = Task(
        description=(
            "Read the candidate's resume with read_google_doc using document: "
            "{resume_doc}\n\n"
            "REMEMBERED ANSWERS from previous sessions (trust these, do NOT ask again):\n"
            "{skill_memory}\n\n"
            "For EACH required and nice-to-have skill/technology from the job analysis:\n"
            "1. If the resume explicitly shows it -> status 'in_resume', quote evidence. "
            "   (The resume wins over memory.)\n"
            "2. Else if it is in REMEMBERED 'HAVE' -> status 'confirmed_by_user', "
            "   evidence = '(from memory) ' + the remembered explanation. Do NOT ask.\n"
            "3. Else if it is in REMEMBERED 'DON'T HAVE' -> status 'denied_by_user', "
            "   evidence = '(from memory) no'. Do NOT ask.\n"
            "   Treat obvious variants as the same skill (e.g. 'Node.js' = 'NodeJS', "
            "   'Postgres' = 'PostgreSQL').\n"
            "4. Otherwise you MUST call ask_human (one skill per question): "
            "   'Do you know <skill>? If yes, where did you use it (company/project) "
            "   and how?'.\n"
            "   - User says yes -> 'confirmed_by_user' with their explanation.\n"
            "   - User says no / not really -> 'denied_by_user'.\n"
            "Use the plain skill name in 'skill' (e.g. 'Docker', not 'Docker experience'). "
            "Never guess."
        ),
        expected_output="SkillVerification JSON.",
        agent=verifier,
        context=[analyze_task],
        output_pydantic=SkillVerification,
    )

    write_task = Task(
        description=(
            "Read the original resume with read_google_doc using document: {resume_doc}\n"
            "Using the job analysis and the skill verification, rewrite the resume "
            "tailored to this job:\n"
            "- Reword the summary for the role.\n"
            "- Reorder/emphasize relevant experience and skills, using job keywords.\n"
            "- Add confirmed_by_user skills in the right experience/skills section, "
            "  based ONLY on what the user said.\n"
            "- If a fact is not in the resume or the skill verification, leave it out.\n"
            f"{TRUTH_RULES}\n"
            "Format: '# ' for name, '## ' for sections, '### ' for roles, '- ' bullets, "
            "no bold/markdown tables. In 'changes', list every change and why.\n"
            f"{OUTPUT_RULES}"
        ),
        expected_output="TailoredResume JSON.",
        agent=writer,
        context=[analyze_task, verify_task],
        output_pydantic=TailoredResume,
    )

    crew = Crew(
        agents=[analyst, verifier, writer],
        tasks=[analyze_task, verify_task, write_task],
        process=Process.sequential,
        verbose=False,
    )
    return crew, verify_task


def revise_resume(llm: LLM, read_tool, previous: TailoredResume,
                  verification_json: str, feedback: str) -> TailoredResume:
    writer = _writer_agent(llm, read_tool, allow_questions=True)
    task = Task(
        description=(
            "Here is a tailored resume draft:\n----\n{draft}\n----\n"
            "Skill verification (source of truth):\n{verification}\n\n"
            "Candidate feedback to apply:\n{feedback}\n\n"
            "Apply the feedback. Use ask_human ONLY to clarify a new skill/experience "
            "mentioned in the feedback, never to ask for approval.\n"
            f"{TRUTH_RULES}\n"
            "Same format as before. In 'changes', list all changes vs the ORIGINAL resume.\n"
            f"{OUTPUT_RULES}"
        ),
        expected_output="TailoredResume JSON.",
        agent=writer,
        output_pydantic=TailoredResume,
    )
    crew = Crew(agents=[writer], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff(inputs={
        "draft": previous.resume_markdown,
        "verification": verification_json,
        "feedback": feedback,
    })
    return to_tailored(result)