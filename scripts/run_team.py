import os
import re
import shutil
import subprocess
from github import Github, Auth
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool
from crewai_tools import FileWriterTool, FileReadTool

# ---------------------------------------------------------
# 1. Load GitHub Actions Context & Secrets
# ---------------------------------------------------------
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]
ISSUE_NUMBER = int(os.environ["ISSUE_NUMBER"])
ISSUE_TITLE = os.environ["ISSUE_TITLE"]
ISSUE_BODY = os.environ.get("ISSUE_BODY", "")

if os.path.exists("project"):
    shutil.rmtree("project")
os.makedirs("project", exist_ok=True)

llm = LLM(
    model="gemini/gemini-3.1-flash-lite-preview",
    temperature=0.2,
    max_retries=5
)

# ---------------------------------------------------------
# 2. Define Real Tools (Files, Quality Gate, GitHub PR)
# ---------------------------------------------------------
file_writer = FileWriterTool()
file_reader = FileReadTool()

@tool("Run Quality Gate")
def run_quality_gate(project_dir: str = "project") -> str:
    """Runs Ruff linter, Bandit security scanner, and Pytest across project_dir."""
    ruff_res = subprocess.run(
        ["ruff", "check", project_dir],
        capture_output=True,
        text=True
    )
    bandit_res = subprocess.run(
        ["bandit", "-r", project_dir, "-x", "*test*"],
        capture_output=True,
        text=True
    )
    pytest_res = subprocess.run(
        ["pytest", "-v", project_dir],
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
    """Creates a feature branch from main, uploads all files in project/, and opens a Pull Request."""
    try:
        g = Github(auth=Auth.Token(GITHUB_TOKEN))
        repo = g.get_repo(GITHUB_REPO)
        default_branch = repo.default_branch
        source_branch = repo.get_branch(default_branch)

        # Ensure unique branch tied to issue number
        safe_branch = f"ai/issue-{ISSUE_NUMBER}-{re.sub(r'[^a-zA-Z0-9-]', '-', branch_name)[:30]}"
        repo.create_git_ref(ref=f"refs/heads/{safe_branch}", sha=source_branch.commit.sha)

        uploaded_files = []
        for root, _, files in os.walk("project"):
            for filename in files:
                if "__pycache__" in root or filename.endswith(".pyc"):
                    continue
                local_path = os.path.join(root, filename)
                with open(local_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Namespace files under project/issue_<N>/ so separate issues never collide
                repo_path = local_path.replace("project/", f"project/issue_{ISSUE_NUMBER}/", 1)
                repo.create_file(
                    path=repo_path,
                    message=f"feat(issue-{ISSUE_NUMBER}): add {repo_path}",
                    content=content,
                    branch=safe_branch
                )
                uploaded_files.append(repo_path)

        full_pr_body = f"Closes #{ISSUE_NUMBER}\n\n{pr_body}"
        pr = repo.create_pull(
            title=pr_title,
            body=full_pr_body,
            head=safe_branch,
            base=default_branch
        )

        # Comment on the triggering GitHub Issue with the PR link
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
    llm=llm,
    verbose=True
)

architect = Agent(
    role="System Architect",
    goal="Design modular Python file structures, classes, and exact function signatures.",
    backstory="You are a senior software architect who ensures modules are cleanly decoupled and testable.",
    llm=llm,
    verbose=True
)

developer = Agent(
    role="Backend Python Developer",
    goal="Write clean, PEP-8 compliant, secure Python code and save it directly to disk using FileWriterTool.",
    backstory="You are a senior Python engineer. You always output raw, valid Python code with zero markdown fences or unused imports.",
    tools=[file_writer, file_reader],
    llm=llm,
    verbose=True
)

qa_security_engineer = Agent(
    role="QA & AppSec Automation Engineer",
    goal="Write pytest suites, run Run Quality Gate (Ruff, Bandit, Pytest), and fix all issues until OVERALL_PASSED is True.",
    backstory=(
        "You enforce strict quality gates. You never pass code with linter warnings, security vulnerabilities, "
        "or failing tests. When Run Quality Gate reports failures, you read and patch the files with FileWriterTool and re-run until OVERALL_PASSED: True."
    ),
    tools=[file_writer, file_reader, run_quality_gate],
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
    description="Write a concise PRD for GitHub Issue #{issue_number} - '{issue_title}':\n{issue_body}",
    expected_output="Structured functional requirements and 5 edge-case acceptance criteria.",
    agent=product_manager
)

task_arch = Task(
    description=(
        "Based on the PRD, define the exact file layout inside `project/` (e.g., `project/solution.py`), "
        "class names, method signatures, and thread-safety or error-handling primitives."
    ),
    expected_output="Technical architecture spec with exact file paths and method signatures.",
    agent=architect
)

task_dev = Task(
    description=(
        "Implement the Python module following the architecture spec. "
        "Save raw, valid Python code (NO ```python markdown fences) into `project/` using `FileWriterTool`."
    ),
    expected_output="Confirmation that source files are saved in `project/`.",
    agent=developer
)

task_qa = Task(
    description=(
        "1. Read all source files in `project/` using `FileReadTool`.\n"
        "2. Write a comprehensive pytest test suite in `project/test_solution.py` using `FileWriterTool` (raw Python only, no markdown fences).\n"
        "3. Execute `Run Quality Gate` on `project`.\n"
        "4. If OVERALL_PASSED is False, fix all Ruff lint warnings, Bandit security issues, or Pytest failures in `project/` using `FileWriterTool` and re-run `Run Quality Gate` until OVERALL_PASSED is True."
    ),
    expected_output="Verified Run Quality Gate output confirming OVERALL_PASSED: True.",
    agent=qa_security_engineer
)

task_devops = Task(
    description=(
        "Once QA confirms OVERALL_PASSED: True, use `Ship Code via GitHub PR` with a short descriptive slug "
        "(e.g., `feature-impl`) to push `project/` and open a Pull Request summarizing the feature, security scan, and test results."
    ),
    expected_output="Live GitHub Pull Request URL.",
    agent=devops_engineer
)

# ---------------------------------------------------------
# 5. Execute the Autonomous Team
# ---------------------------------------------------------
if __name__ == "__main__":
    dev_team = Crew(
        agents=[product_manager, architect, developer, qa_security_engineer, devops_engineer],
        tasks=[task_prd, task_arch, task_dev, task_qa, task_devops],
        process=Process.sequential,
        verbose=True
    )

    result = dev_team.kickoff(inputs={
        "issue_number": ISSUE_NUMBER,
        "issue_title": ISSUE_TITLE,
        "issue_body": ISSUE_BODY
    })
    print("\n=== AUTONOMOUS BUILD COMPLETE ===\n", result)
