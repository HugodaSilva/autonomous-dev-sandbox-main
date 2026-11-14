import os
import re
import time
import subprocess
from github import Github, Auth, GithubException
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool
from crewai_tools import FileReadTool, DirectoryReadTool

# ---------------------------------------------------------
# 1. Load GitHub Actions Context & Secrets
# ---------------------------------------------------------
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]
ISSUE_NUMBER = int(os.environ["ISSUE_NUMBER"])
ISSUE_TITLE = os.environ["ISSUE_TITLE"]
ISSUE_BODY = os.environ.get("ISSUE_BODY", "")

PROTECTED_PREFIXES = (".git", ".github", "scripts", "__pycache__", ".pytest_cache", ".ruff_cache")

def rate_limit_pause(step_output):
    """Pauses briefly between agent steps to stay under Gemini's 15 RPM Free Tier cap."""
    time.sleep(6)

def get_repo_tree(root_dir: str = ".") -> str:
    """Returns a list of existing editable files in the repository."""
    paths = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "scripts")]
        for f in sorted(files):
            rel_path = os.path.normpath(os.path.join(root, f))
            if not rel_path.startswith(PROTECTED_PREFIXES):
                paths.append(rel_path)
    return "\n".join(paths) if paths else "(Empty repository - no source files yet)"

llm = LLM(
    model="gemini/gemini-3.1-flash-lite-preview",
    temperature=0.2,
    max_retries=5
)

# ---------------------------------------------------------
# 2. Define Real Tools (Repo Files, Quality Gate, GitHub PR)
# ---------------------------------------------------------
dir_reader = DirectoryReadTool(directory=".")
file_reader = FileReadTool()

@tool("Safe File Writer")
def safe_write_file(file_path: str, content: str) -> str:
    """Creates or updates a file at the exact repository path (e.g. src/models/service_schema.py)."""
    clean_path = os.path.normpath(file_path).lstrip("/")
    if clean_path.startswith(PROTECTED_PREFIXES) or clean_path == "requirements.txt":
        return f"ERROR: '{clean_path}' is a protected orchestration file and cannot be modified."

    os.makedirs(os.path.dirname(clean_path) or ".", exist_ok=True)
    with open(clean_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"SUCCESS: Wrote {len(content)} bytes to '{clean_path}'."

@tool("Run Quality Gate")
def run_quality_gate(target_dir: str = ".") -> str:
    """Runs Ruff linter, Bandit security scanner, and Pytest across the repository."""
    ruff_res = subprocess.run(
        ["ruff", "check", target_dir, "--exclude", "scripts,.github"],
        capture_output=True,
        text=True
    )
    bandit_res = subprocess.run(
        ["bandit", "-r", target_dir, "-x", "./scripts,./.github,*test*"],
        capture_output=True,
        text=True
    )
    pytest_res = subprocess.run(
        ["pytest", "-v", "--ignore=scripts"],
        capture_output=True,
        text=True
    )

    overall_passed = (
        ruff_res.returncode == 0
        and bandit_res.returncode == 0
        and pytest_res.returncode == 0
    )

    return (
        f"OVERALL_PASSED: {overall_passed}\n"
        f"--- RUFF LINTER (Exit {ruff_res.returncode}) ---\n{ruff_res.stdout}{ruff_res.stderr}\n"
        f"--- BANDIT SECURITY (Exit {bandit_res.returncode}) ---\n{bandit_res.stdout}{bandit_res.stderr}\n"
        f"--- PYTEST SUITE (Exit {pytest_res.returncode}) ---\n{pytest_res.stdout}{pytest_res.stderr}"
    )

@tool("Ship Code via GitHub PR")
def create_github_pr(branch_name: str, pr_title: str, pr_body: str) -> str:
    """Commits all newly created or modified repository files to a feature branch and opens a Pull Request."""
    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
            check=True
        )
        changed_files = []
        for line in status_res.stdout.splitlines():
            if not line.strip():
                continue
            rel_path = line[3:].strip().strip('"')
            if not rel_path.startswith(PROTECTED_PREFIXES) and rel_path != "requirements.txt":
                changed_files.append(rel_path)

        if not changed_files:
            return "ERROR: No modified or newly created source files detected in workspace."

        g = Github(auth=Auth.Token(GITHUB_TOKEN))
        repo = g.get_repo(GITHUB_REPO)
        default_branch = repo.default_branch
        source_branch = repo.get_branch(default_branch)

        safe_branch = f"ai/issue-{ISSUE_NUMBER}-{re.sub(r'[^a-zA-Z0-9-]', '-', branch_name)[:30]}"
        repo.create_git_ref(ref=f"refs/heads/{safe_branch}", sha=source_branch.commit.sha)

        uploaded_files = []
        for repo_path in changed_files:
            if not os.path.exists(repo_path) or os.path.isdir(repo_path):
                continue
            with open(repo_path, "r", encoding="utf-8") as f:
                content = f.read()

            try:
                existing = repo.get_contents(repo_path, ref=safe_branch)
                repo.update_file(
                    path=repo_path,
                    message=f"fix/feat(issue-{ISSUE_NUMBER}): update {repo_path}",
                    content=content,
                    sha=existing.sha,
                    branch=safe_branch
                )
            except GithubException as ge:
                if ge.status == 404:
                    repo.create_file(
                        path=repo_path,
                        message=f"feat(issue-{ISSUE_NUMBER}): add {repo_path}",
                        content=content,
                        branch=safe_branch
                    )
                else:
                    raise
            uploaded_files.append(repo_path)

        full_pr_body = f"Closes #{ISSUE_NUMBER}\n\n{pr_body}"
        pr = repo.create_pull(
            title=pr_title,
            body=full_pr_body,
            head=safe_branch,
            base=default_branch
        )

        issue = repo.get_issue(number=ISSUE_NUMBER)
        issue.create_comment(
            f"Autonomous Dev Team completed the build and passed Ruff, Bandit, and Pytest.\n\n"
            f"**Pull Request:** {pr.html_url}"
        )
        return f"SUCCESS: Uploaded {uploaded_files} and opened PR #{pr.number}: {pr.html_url}"
    except Exception as e:
        return f"ERROR creating PR: {str(e)}"

# ---------------------------------------------------------
# 3. Define the 5 Autonomous Agents
# ---------------------------------------------------------
product_manager = Agent(
    role="Product Manager",
    goal="Convert GitHub Issue requirements into clear functional specs and edge-case acceptance criteria.",
    backstory="You are a technical PM who defines unambiguous requirements and edge cases.",
    tools=[dir_reader, file_reader],
    llm=llm,
    verbose=True
)

architect = Agent(
    role="System Architect",
    goal="Design modular Python file structures, classes, and exact function signatures at the target repo paths.",
    backstory="You are a senior software architect who ensures modules are cleanly decoupled, testable, and placed at exact repository paths.",
    tools=[dir_reader, file_reader],
    llm=llm,
    verbose=True
)

developer = Agent(
    role="Backend Python Developer",
    goal="Write clean, PEP-8 compliant, secure Python code and save it directly to the target repo paths using Safe File Writer.",
    backstory=(
        "You are a senior Python engineer. Before modifying any existing file, read it first with FileReadTool. "
        "You always output raw, valid Python code with zero markdown fences or unused imports."
    ),
    tools=[dir_reader, file_reader, safe_write_file],
    llm=llm,
    verbose=True
)

qa_security_engineer = Agent(
    role="QA & AppSec Automation Engineer",
    goal="Write pytest suites, run Run Quality Gate (Ruff, Bandit, Pytest), and fix all issues until OVERALL_PASSED is True.",
    backstory=(
        "You enforce strict quality gates. You never pass code with linter warnings, security vulnerabilities, "
        "or failing tests. When Run Quality Gate reports failures, you read and patch the files with Safe File Writer and re-run until OVERALL_PASSED: True."
    ),
    tools=[dir_reader, file_reader, safe_write_file, run_quality_gate],
    llm=llm,
    verbose=True
)

devops_engineer = Agent(
    role="DevOps Release Engineer",
    goal="Ship verified code by creating a feature branch and opening a GitHub Pull Request.",
    backstory="You manage CI/CD releases and only ship code after QA confirms OVERALL_PASSED: True.",
    tools=[create_github_pr],
    llm=llm,
    verbose=True
)

# ---------------------------------------------------------
# 4. Define End-to-End Tasks
# ---------------------------------------------------------
task_prd = Task(
    description=(
        "Current repository files:\n{repo_tree}\n\n"
        "Write a concise PRD for GitHub Issue #{issue_number} - '{issue_title}':\n{issue_body}"
    ),
    expected_output="Structured functional requirements and 5 edge-case acceptance criteria.",
    agent=product_manager
)

task_arch = Task(
    description=(
        "Based on the PRD and existing repository files ({repo_tree}), define the exact repository file paths "
        "requested by the GitHub Issue (for example, `src/models/service_schema.py` and `src/models/__init__.py`). "
        "Do NOT use a `project/` wrapper folder. Specify class names, method signatures, and validation rules."
    ),
    expected_output="Technical architecture spec with exact repository file paths and method signatures.",
    agent=architect
)

task_dev = Task(
    description=(
        "Implement the Python modules following the architecture spec at the exact repository paths "
        "(e.g., `src/models/service_schema.py` and `src/models/__init__.py`). "
        "Save raw, valid Python code (NO ```python markdown fences) using `Safe File Writer`."
    ),
    expected_output="Confirmation that source files are saved at the target repository paths.",
    agent=developer
)

task_qa = Task(
    description=(
        "1. Read all created/modified source files using `FileReadTool`.\n"
        "2. Write a comprehensive pytest test suite under `tests/` (e.g., `tests/test_issue_{issue_number}.py`) using `Safe File Writer` (raw Python only, no markdown fences).\n"
        "3. Execute `Run Quality Gate` on `.`.\n"
        "4. If OVERALL_PASSED is False, fix all Ruff lint warnings, Bandit security issues, or Pytest failures using `Safe File Writer` and re-run `Run Quality Gate` until OVERALL_PASSED is True."
    ),
    expected_output="Verified Run Quality Gate output confirming OVERALL_PASSED: True.",
    agent=qa_security_engineer
)

task_devops = Task(
    description=(
        "Once QA confirms OVERALL_PASSED: True, use `Ship Code via GitHub PR` with a short descriptive slug "
        "(e.g., `domain-models`) to commit all new/modified repository files and open a Pull Request summarizing the feature, security scan, and test results."
    ),
    expected_output="Live GitHub Pull Request URL.",
    agent=devops_engineer
)

# ---------------------------------------------------------
# 5. Execute the Autonomous Team
# ---------------------------------------------------------
if __name__ == "__main__":
    current_tree = get_repo_tree(".")
    dev_team = Crew(
        agents=[product_manager, architect, developer, qa_security_engineer, devops_engineer],
        tasks=[task_prd, task_arch, task_dev, task_qa, task_devops],
        process=Process.sequential,
        max_rpm=5,
        step_callback=rate_limit_pause,
        verbose=True
    )

    result = dev_team.kickoff(inputs={
        "repo_tree": current_tree,
        "issue_number": ISSUE_NUMBER,
        "issue_title": ISSUE_TITLE,
        "issue_body": ISSUE_BODY
    })
    print("\n=== AUTONOMOUS BUILD COMPLETE ===\n", result)
