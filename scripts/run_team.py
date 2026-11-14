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


def rate_limit_pause(_output=None):
    """Pauses between agent steps and tasks to stay safely under Gemini's 15 RPM Free Tier cap."""
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


def strip_markdown_fences(content: str) -> str:
    """Removes accidental ```python ... ``` wrappers emitted by LLMs."""
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines) + "\n"
    return content


llm = LLM(
    model="gemini/gemini-3.1-flash-lite-preview",
    temperature=0.1,
    max_retries=6,
    timeout=120
)

# ---------------------------------------------------------
# 2. Define Real Tools (Repo Files, Quality Gate, GitHub PR)
# ---------------------------------------------------------
dir_reader = DirectoryReadTool(directory=".")
file_reader = FileReadTool()


@tool("Safe File Writer")
def safe_write_file(file_path: str, content: str) -> str:
    """Creates or updates a file at the exact repository path (e.g. src/catalog/repository.py)."""
    clean_path = os.path.normpath(file_path).lstrip("/")
    if clean_path.startswith(PROTECTED_PREFIXES) or clean_path == "requirements.txt":
        return f"ERROR: '{clean_path}' is a protected orchestration file and cannot be modified."

    dir_name = os.path.dirname(clean_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
        # Ensure every intermediate Python package directory has an __init__.py so pytest imports work reliably
        if clean_path.endswith(".py"):
            parts = dir_name.split(os.sep)
            for i in range(1, len(parts) + 1):
                pkg_dir = os.sep.join(parts[:i])
                init_file = os.path.join(pkg_dir, "__init__.py")
                if not os.path.exists(init_file):
                    with open(init_file, "w", encoding="utf-8") as init_f:
                        init_f.write("")

    clean_content = strip_markdown_fences(content) if clean_path.endswith(".py") else content
    with open(clean_path, "w", encoding="utf-8") as f:
        f.write(clean_content)
    return f"SUCCESS: Wrote {len(clean_content)} bytes to '{clean_path}'."


@tool("Run Quality Gate")
def run_quality_gate(target_dir: str = ".") -> str:
    """Auto-formats with Ruff, then runs Ruff linter, Bandit security scanner, and Pytest."""
    # Step 1: Auto-fix import sorting (I001) and formatting so the agent doesn't burn RPM on trivial style fixes
    subprocess.run(
        ["ruff", "check", "--fix", target_dir, "--exclude", "scripts,.github"],
        capture_output=True,
        text=True
    )
    subprocess.run(
        ["ruff", "format", target_dir, "--exclude", "scripts,.github"],
        capture_output=True,
        text=True
    )

    # Step 2: Run strict verification checks
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

    # Ensure repository root is on PYTHONPATH so `from src.pkg.mod import ...` always resolves
    pytest_env = os.environ.copy()
    existing_pythonpath = pytest_env.get("PYTHONPATH", "")
    pytest_env["PYTHONPATH"] = f".:{existing_pythonpath}" if existing_pythonpath else "."

    pytest_res = subprocess.run(
        ["pytest", "-v", "--ignore=scripts"],
        capture_output=True,
        text=True,
        env=pytest_env
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

        safe_slug = re.sub(r"[^a-zA-Z0-9-]", "-", branch_name).strip("-")[:30] or "feature"
        safe_branch = f"ai/issue-{ISSUE_NUMBER}-{safe_slug}"
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
    backstory="You are a technical PM who defines unambiguous requirements and edge cases without redundant file scans.",
    tools=[file_reader],
    llm=llm,
    max_iter=5,
    verbose=True
)

architect = Agent(
    role="System Architect",
    goal="Design modular Python file structures, classes, and exact function signatures at the target repo paths.",
    backstory=(
        "You are a senior software architect who ensures modules are cleanly decoupled, testable, "
        "and use absolute root-relative imports (e.g., `from src.catalog.models import ...`)."
    ),
    tools=[file_reader],
    llm=llm,
    max_iter=5,
    verbose=True
)

developer = Agent(
    role="Backend Python Developer",
    goal="Write clean, PEP-8 compliant, secure Python code and save it directly to the target repo paths using Safe File Writer.",
    backstory=(
        "You are a senior Python engineer. Read existing target files first if they already appear in the repo tree. "
        "Always use absolute root-relative imports (e.g., `from src.pkg.module import Class`) and output complete, valid Python code."
    ),
    tools=[file_reader, safe_write_file],
    llm=llm,
    max_iter=8,
    verbose=True
)

qa_security_engineer = Agent(
    role="QA & AppSec Automation Engineer",
    goal="Write pytest suites, run Run Quality Gate (Ruff, Bandit, Pytest), and fix all issues until OVERALL_PASSED is True.",
    backstory=(
        "You enforce strict quality gates. Write clean pytest tests using root-relative imports "
        "(e.g., `from src.pkg.module import ...`), execute Run Quality Gate once, and patch only reported failures."
    ),
    tools=[file_reader, safe_write_file, run_quality_gate],
    llm=llm,
    max_iter=10,
    verbose=True
)

devops_engineer = Agent(
    role="DevOps Release Engineer",
    goal="Ship verified code by creating a feature branch and opening a GitHub Pull Request.",
    backstory="You manage CI/CD releases and only ship code after QA confirms OVERALL_PASSED: True.",
    tools=[create_github_pr],
    llm=llm,
    max_iter=4,
    verbose=True
)

# ---------------------------------------------------------
# 4. Define End-to-End Tasks
# ---------------------------------------------------------
task_prd = Task(
    description=(
        "Current repository files:\n{repo_tree}\n\n"
        "Write a concise PRD for GitHub Issue #{issue_number} - '{issue_title}':\n{issue_body}\n"
        "Do not call directory listing tools unless you need to read a specific file listed above."
    ),
    expected_output="Structured functional requirements and 5 edge-case acceptance criteria.",
    agent=product_manager
)

task_arch = Task(
    description=(
        "Based on the PRD and existing repository files ({repo_tree}), define the exact repository file paths "
        "requested by GitHub Issue #{issue_number} ('{issue_title}').\n"
        "Rules:\n"
        "- Follow the exact folder paths specified in the issue (e.g., `src/...`). Never create a `project/` wrapper.\n"
        "- Specify absolute root-relative Python imports (e.g., `from src.module import ...`) across all files.\n"
        "- Specify class names, method signatures, type hints, and validation rules."
    ),
    expected_output="Technical architecture spec with exact repository file paths, import paths, and method signatures.",
    context=[task_prd],
    agent=architect
)

task_dev = Task(
    description=(
        "Implement the Python modules following the architecture spec at the exact repository paths. "
        "Use `Safe File Writer` to save complete, valid Python code with proper type hints, docstrings, and "
        "root-relative imports. List every file written and its public signatures in your final response."
    ),
    expected_output="List of saved source file paths and their implemented classes/functions.",
    context=[task_prd, task_arch],
    agent=developer
)

task_qa = Task(
    description=(
        "1. Review the implemented modules from the developer's summary (read files with `FileReadTool` only if details are missing).\n"
        "2. Write a comprehensive pytest test suite at `tests/test_issue_{issue_number}.py` using `Safe File Writer`.\n"
        "   - Import modules using root-relative paths (e.g., `from src.pkg.mod import ...`).\n"
        "   - Cover normal behavior and all 5 edge-case acceptance criteria from the PRD.\n"
        "3. Execute `Run Quality Gate` on `.` (note: Ruff auto-fix and format run automatically inside the tool).\n"
        "4. If OVERALL_PASSED is False, fix only the specific Ruff, Bandit, or Pytest errors using `Safe File Writer` and re-run `Run Quality Gate` until OVERALL_PASSED is True."
    ),
    expected_output="Verified Run Quality Gate output confirming OVERALL_PASSED: True.",
    context=[task_prd, task_arch, task_dev],
    agent=qa_security_engineer
)

task_devops = Task(
    description=(
        "Once QA confirms OVERALL_PASSED: True, call `Ship Code via GitHub PR` with a short descriptive slug "
        "(e.g., `catalog-repo`) to commit all new/modified repository files and open a Pull Request summarizing "
        "the feature, security scan, and test results."
    ),
    expected_output="Live GitHub Pull Request URL.",
    context=[task_prd, task_qa],
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
        task_callback=rate_limit_pause,
        verbose=True
    )

    result = dev_team.kickoff(inputs={
        "repo_tree": current_tree,
        "issue_number": ISSUE_NUMBER,
        "issue_title": ISSUE_TITLE,
        "issue_body": ISSUE_BODY
    })
    print("\n=== AUTONOMOUS BUILD COMPLETE ===\n", result)
