import sys
import threading
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

# One global lock: only ONE question can be on screen at any time,
# even if the LLM fires several ask_human calls in parallel.
_ASK_LOCK = threading.Lock()
_counter = {"n": 0}


class AskHumanInput(BaseModel):
    question: str = Field(..., description="A clear, single question for the candidate.")


class AskHumanTool(BaseTool):
    name: str = "ask_human"
    description: str = (
        "Ask the candidate ONE question in the terminal and get their answer. "
        "Use it whenever you are NOT sure the candidate has a skill/technology. "
        "Ask ONE skill per call and wait for the answer before asking the next."
    )
    args_schema: Type[BaseModel] = AskHumanInput
    cache_function: object = lambda _args=None, _result=None: False  # never cache

    def _run(self, question: str) -> str:
        with _ASK_LOCK:
            _counter["n"] += 1
            sys.stdout.flush()
            print("\n" + "=" * 70)
            print(f"QUESTION {_counter['n']}: {question}")
            print("(answer 'no' if you don't have this skill)")
            print("=" * 70, flush=True)
            answer = ""
            while not answer.strip():
                answer = input("Your answer > ")
            print("-" * 70 + "\nThanks, recorded.\n", flush=True)
            return answer.strip()