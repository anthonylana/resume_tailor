# resume_tailor

An AI agent that reads your resume from **Google Docs**, analyzes a **job description**, asks you about any skills it can't verify, and creates a **new, tailored Google Doc**. Your original resume is **never modified**.

Built with [CrewAI](https://www.crewai.com/), an MCP server for Google Docs, and LLMs served through the [Mammouth AI](https://mammouth.ai) API.

---

## Features

- **Read-only access to your original resume.** The app can't edit it because the OAuth scope is `documents.readonly`.
- **Flexible job posting input:** a Google Doc, a local file, pasted text, or `job_posting.txt`.
- **Honest tailoring.** The agent never invents skills, employers, dates, or metrics. If a required skill isn't in your resume, it **asks you**, one question at a time.
- **Skill memory.** Your answers are saved to `skills_memory.json`, so you're not asked the same question twice.
- **Review before anything is created.** You see a list of changes, a diff, and the full tailored resume, then choose to approve, revise with feedback, or quit.
- **Creates a brand-new Google Doc** with proper headings and bullet lists.
- **Local backup.** If Google Doc creation fails, the resume is saved to `tailored_resume.md`.

---

## How it works

```
                ┌──────────────────────┐
Job posting ──▶ │ 1. Job Posting       │  extracts title, company,
                │    Analyst           │  required / nice-to-have skills
                └──────────┬───────────┘
                           ▼
Resume (GDoc) ─▶┌──────────────────────┐  ◀── skills_memory.json
                │ 2. Skills Verifier   │  asks YOU about missing skills
                └──────────┬───────────┘  ──▶ saves answers to memory
                           ▼
                ┌──────────────────────┐
                │ 3. Resume Writer     │  rewrites resume (JSON output)
                └──────────┬───────────┘
                           ▼
                Review: [a]pprove / [r]evise / [q]uit
                           ▼
                New Google Doc created (original untouched)
```

Google Docs access goes through a local **MCP server** (`gdocs_mcp_server.py`) that exposes only two tools:

| Tool | Purpose |
|---|---|
| `read_google_doc` | Reads a doc and returns text (`#` headings, `-` bullets) |
| `create_google_doc` | Creates a **new** doc. It cannot modify existing ones. |

---

## Project structure

```
resume_tailor/
├── main.py               # Entry point: CLI flow, review loop, doc creation
├── crew_setup.py         # Agents, tasks, LLM config, output parsing
├── human_tools.py        # ask_human tool (terminal Q&A, one question at a time)
├── skill_memory.py       # Persistent skill memory (skills_memory.json)
├── gdocs_mcp_server.py   # MCP server: read / create Google Docs
├── google_auth.py        # Google OAuth helper (run once to log in)
├── job_posting.txt       # Default job posting input (optional)
├── .env                  # API keys (not committed)
├── credentials.json      # Google OAuth client (not committed)
├── token.json            # Google OAuth token, generated (not committed)
└── skills_memory.json    # Your skill answers, generated (not committed)
```

---

## Requirements

- Python **3.10+**
- A Google account
- A Mammouth AI API key

---

## 1. Installation

```bash
cd resume_tailor
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install crewai "crewai-tools[mcp]" mcp python-dotenv \
            google-api-python-client google-auth google-auth-oauthlib
```

Optionally, save the dependencies to a `requirements.txt`:

```bash
pip freeze > requirements.txt
```

---

## 2. Google Cloud setup (one time)

The app needs an OAuth client so it can read your docs and create new ones **on your behalf**.

### 2.1 Create a project
1. Go to <https://console.cloud.google.com/>.
2. Open the project selector at the top, click **New Project**, name it (e.g. `resume-tailor`), and click **Create**.
3. Make sure the new project is selected.

### 2.2 Enable the Google Docs API
1. Go to **APIs & Services → Library**.
2. Search for **Google Docs API** and click **Enable**.
3. (Optional) Enable the **Google Drive API** too. It isn't required for the current code, but it's useful if you later want to move docs into folders.

### 2.3 Configure the OAuth consent screen
1. Go to **APIs & Services → OAuth consent screen** (called **Google Auth Platform → Branding / Audience** in newer consoles).
2. User type: **External**. Fill in the app name and your email.
3. **Scopes:** you can skip this. The app requests its scopes at login.
4. **Test users:** **add your own Gmail address.** This step is required while the app is in "Testing" mode, or login will fail with `access_denied`.

### 2.4 Create OAuth credentials
1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Desktop app**.
3. Click **Create**, then **Download JSON**.
4. Rename the file to **`credentials.json`** and put it in the `resume_tailor/` folder.

### 2.5 Log in once
```bash
python google_auth.py
```
A browser opens. Choose your account and approve the permissions. You may see "Google hasn't verified this app". Click **Continue**, since it's your own app. A `token.json` file is then created.

**Permissions requested:**

| Scope | What it allows |
|---|---|
| `documents.readonly` | Read your docs. Editing them is impossible. |
| `drive.file` | Create and edit **only** the files this app creates |

> If you ever change `SCOPES`, delete `token.json` and run `python google_auth.py` again.
> `google_auth.py` also detects a token with missing scopes and asks you to log in again.

---

## 3. Configure the LLM (`.env`)

Create `resume_tailor/.env`:

```env
MAMMOUTH_API_KEY=your_mammouth_api_key_here
MAMMOUTH_MODEL=openai/gpt-4.1
```

- `MAMMOUTH_MODEL` is passed to CrewAI/LiteLLM. Because the app uses Mammouth's OpenAI-compatible endpoint, prefix the model name with `openai/`.
- Use a model that is good at **tool calling and JSON output**, such as GPT-4.1, Claude Sonnet, or Gemini Pro. Small models often produce broken JSON.

---

## 4. Prepare your resume in Google Docs

For best results, format your resume with Google Docs **styles**:

| Element | Google Docs style |
|---|---|
| Your name | *Title* or *Heading 1* |
| Sections (Experience, Skills…) | *Heading 2* |
| Job roles | *Heading 3* |
| Achievements | Bulleted list |

Copy the doc URL, e.g. `https://docs.google.com/document/d/1AbC.../edit`. You can paste either the full URL or just the ID.

> Tables are read but flattened. Simple, single-column resumes give the cleanest output.

---

## 5. Usage

```bash
python main.py
```

### Step by step

1. **Resume:** paste your resume's Google Doc URL or ID.
2. **Job posting:** choose a source:
   ```
   1) Google Doc URL/ID
   2) Local text file (any path)
   3) Paste it here            (type END on its own line to finish)
   4) Use job_posting.txt      (default)
   ```
3. **Answer the questions.** For each skill the agent can't find in your resume or memory, it asks:
   ```
   ======================================================================
   QUESTION 1: Do you know Kubernetes? If yes, where did you use it and how?
   (answer 'no' if you don't have this skill)
   ======================================================================
   Your answer > Yes, at Acme Corp I deployed 12 microservices on EKS
   ```
   Be specific. The agent uses **only** what you say.
4. **Review.** You'll see:
   - the list of proposed changes and why each was made
   - a diff of the original vs. the tailored version
   - the full tailored resume
5. **Decide:**
   - `a` approves. You enter a name for the new doc, confirm, and the new Google Doc is created.
   - `r` lets you give feedback (e.g. "shorten the summary, emphasize Python"). The agent revises the resume and shows the review again.
   - `q` quits. Nothing is created.
6. The URL of the new document is printed. Your original resume is unchanged.

---

## 6. Skill memory

Your answers are stored in `skills_memory.json`:

```json
{
  "version": 1,
  "skills": {
    "docker": {
      "name": "Docker",
      "status": "have",
      "evidence": "Used at Acme Corp to containerize Flask APIs",
      "updated": "2026-11-08"
    },
    "rust": { "name": "Rust", "status": "dont_have", "evidence": "no", "updated": "2026-11-08" }
  }
}
```

- Only answers **you gave** are stored. Skills already in your resume are read from the resume each run.
- Name variants match: `Node.js` = `nodejs` = `NodeJS`.
- If your resume shows a skill, the **resume wins over memory**.

| Task | Command |
|---|---|
| List remembered skills | `python skill_memory.py` |
| Forget a skill | `python skill_memory.py --forget "Kubernetes"` |
| Fix an explanation | Edit `skills_memory.json` by hand |
| Reset everything | Delete `skills_memory.json` |

> Answers given during the `[r]evise` loop are **not** saved automatically. Edit the file by hand if needed.

---

## 7. Honesty rules

The writer agent follows strict rules:
- It never invents skills, technologies, employers, dates, titles, metrics, or degrees.
- It uses only facts from your resume or from your confirmed answers.
- Skills you denied never appear.
- Job titles, companies, and dates are kept **exactly** as in the original.
- It may reorder, rephrase, emphasize, and remove less relevant content.

**Always proofread the final document before sending it.**

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `MAMMOUTH_API_KEY missing in .env` | Create `.env` (see section 3) |
| `No valid Google token...` | Run `python google_auth.py` |
| `access_denied` during login | Add your email as a **Test user** (section 2.3) |
| `403 insufficient authentication scopes` | Delete `token.json` and run `python google_auth.py` again |
| `403 ... Google Docs API has not been used` | Enable the Google Docs API (section 2.2) |
| `404 Requested entity was not found` | Wrong doc ID, or the doc isn't shared with your logged-in account |
| Creation failed | The resume is saved to `tailored_resume.md`. Check the printed error |
| Agent shows the resume as a "question" | Make sure the Writer agent has **no** `ask_human` tool |
| `'NoneType' object has no attribute ...` | The model returned bad JSON. Use a stronger model in `MAMMOUTH_MODEL` |
| HTTPS / SSL errors | `SSLKEYLOGFILE` is already removed at startup. Check proxy/VPN settings |
| Need to see what the agents do | Set `verbose=True` on the agents and `Crew` in `crew_setup.py` |

---

## 9. Security & privacy

Never commit secrets or personal data. Add this to `.gitignore`:

```
.env
credentials.json
token.json
skills_memory.json
skills_memory.corrupt.json
tailored_resume.md
.venv/
__pycache__/
```

- Your resume and job posting are sent to the LLM provider (Mammouth AI) for processing.
- To revoke Google access at any time, go to <https://myaccount.google.com/permissions>.

---

## License

MIT (or your choice)