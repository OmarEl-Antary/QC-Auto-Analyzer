import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from jira import JIRA
from groq import Groq
import requests
import time
from datetime import datetime
from dotenv import load_dotenv
import os

# ✅ بيقرأ البيانات من ملف .env
load_dotenv()

JIRA_SERVER = os.getenv("JIRA_SERVER")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MY_CHAT_ID = os.getenv("MY_CHAT_ID")

# --- [إعدادات الربط] ---
client = Groq(api_key=GROQ_API_KEY)
jira = JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        response = requests.post(url, json={
            "chat_id": MY_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        })
        print(f"Telegram: {response.json().get('ok')}")
    except Exception as e:
        print(f"Telegram Error: {e}")

def already_analyzed(issue):
    comments = jira.comments(issue)
    for comment in comments:
        if "AI Analysis (Groq)" in comment.body:
            return True
    return False

def has_update_after_analysis(issue):
    comments = jira.comments(issue)
    last_analysis_time = None

    for comment in comments:
        if "AI Analysis (Groq)" in comment.body:
            last_analysis_time = comment.updated

    if last_analysis_time is None:
        return False

    issue_with_changelog = jira.issue(issue.key, expand='changelog')
    changelog = issue_with_changelog.changelog

    for history in changelog.histories:
        history_time = history.created
        if history_time > last_analysis_time:
            for item in history.items:
                if item.field in ['summary', 'description', 'acceptanceCriteria']:
                    print(f"Update detected on field: {item.field}")
                    return True
    return False

def process_story(issue):
    story_key = issue.key
    summary = issue.fields.summary
    description = issue.fields.description or "No description provided."

    issue_type = issue.fields.issuetype.name
    if issue_type != "Story":
        print(f"Skipping {story_key} - Not a Story (is {issue_type})")
        return "skipped"

    if already_analyzed(issue):
        if has_update_after_analysis(issue):
            print(f"Update detected on {story_key} - Re-analyzing...")
        else:
            print(f"Skipping {story_key} - Already analyzed and no updates.")
            return "skipped"

    try:
        print(f"\nProcessing: {story_key} - {summary}")

        prompt = f"""
You are a Senior Software QC Specialist with 10+ years of experience.

Analyze the following User Story and generate COMPREHENSIVE test cases.

Story Title: {summary}
Story Description: {description}

Requirements:
- Generate all test case test cases
- 
Don't repeat any test cases 
- Cover: Happy Path, Negative Cases, Edge Cases, UI/UX, Performance, Security
- Format MUST be a table with these exact columns: | # | Title | Steps | Expected Result | Type |
- Type can be: Happy Path / Negative / Edge Case / UI / Security / Performance
- Write everything in Arabic
- Be very detailed in Steps (numbered steps inside each cell)
- Do NOT skip any scenario

Output ONLY the table, nothing else.
"""

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a Senior QC Specialist. Always respond with a detailed markdown table of test cases in Arabic. Never skip edge cases."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="llama-3.1-8b-instant",
            max_tokens=4000,
            temperature=0.3,
        )

        ai_test_cases = chat_completion.choices[0].message.content
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        comment_body = f"AI Analysis (Groq) - {timestamp}\n\n{ai_test_cases}"

        jira.add_comment(story_key, comment_body)
        print(f"Done: {story_key}")

        story_link = f"{JIRA_SERVER}browse/{story_key}"
        send_telegram(f"✅ تم تحليل <a href='{story_link}'>{story_key}</a>\n📝 {summary}")

        time.sleep(2)
        return "done"

    except Exception as e:
        print(f"FAILED {story_key}: {str(e)}")
        send_telegram(f"❌ فشل تحليل {story_key}: {str(e)}")
        return "failed"

def run_regression(regression_story_key, project_name):
    try:
        print(f"\nGenerating Regression Test Cases for: {regression_story_key}")

        old_stories = jira.search_issues(
            f'project = "{project_name}" AND sprint in closedSprints() AND type = Story',
            maxResults=50
        )

        if not old_stories:
            print("No old stories found for Regression!")
            return

        old_features = "\n".join([f"- {s.fields.summary}" for s in old_stories])

        prompt = f"""
You are a Senior Software QC Specialist with 10+ years of experience.

Generate Regression Test Cases (Happy Path ONLY) for the following old features.

Old Features:
{old_features}

Requirements:
- Generate Happy Path test cases ONLY
- One test case per feature minimum
- Format MUST be a table with these exact columns: | # | Feature | Steps | Expected Result |
- Write everything in Arabic

Output ONLY the table, nothing else.
"""

        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a Senior QC Specialist. Generate concise Regression Test Cases in Arabic."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="llama-3.1-8b-instant",
            max_tokens=4000,
            temperature=0.3,
        )

        regression_tc = chat_completion.choices[0].message.content
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        comment_body = f"Regression Test Cases (Groq) - {timestamp}\n\n{regression_tc}"

        jira.add_comment(regression_story_key, comment_body)
        print(f"Regression Done: {regression_story_key}")

    except Exception as e:
        print(f"FAILED Regression: {str(e)}")

def run_jql(jql_query, project_name, regression_story=None):
    print(f"Running JQL: {jql_query}")

    issues = jira.search_issues(jql_query, maxResults=50)
    total = len(issues)
    print(f"Found {total} Stories")

    if total == 0:
        send_telegram(f"⚠️ مفيش Stories اتلاقت في {project_name}!")
        return

    done_stories = []
    skipped_stories = []
    updated_stories = []
    failed_stories = []

    for i, issue in enumerate(issues, 1):
        print(f"\n[{i}/{total}]")
        result = process_story(issue)
        story_link = f"{JIRA_SERVER}browse/{issue.key}"

        if result == "done":
            done_stories.append((issue.key, issue.fields.summary, story_link))
        elif result == "skipped":
            skipped_stories.append((issue.key, issue.fields.summary, story_link))
        elif result == "updated":
            updated_stories.append((issue.key, issue.fields.summary, story_link))
        elif result == "failed":
            failed_stories.append((issue.key, issue.fields.summary, story_link))

    if regression_story:
        run_regression(regression_story, project_name)

    report = f"📊 <b>تقرير {project_name}</b>\n"
    report += f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    report += f"━━━━━━━━━━━━━━━━\n\n"

    if done_stories:
        report += f"✅ <b>تم تحليلهم ({len(done_stories)})</b>\n"
        for key, summary, link in done_stories:
            report += f"• <a href='{link}'>{key}</a> - {summary}\n"
        report += "\n"

    if updated_stories:
        report += f"🔄 <b>تم إعادة تحليلهم ({len(updated_stories)})</b>\n"
        for key, summary, link in updated_stories:
            report += f"• <a href='{link}'>{key}</a> - {summary}\n"
        report += "\n"

    if skipped_stories:
        report += f"⏭ <b>تم تخطيهم ({len(skipped_stories)})</b>\n"
        for key, summary, link in skipped_stories:
            report += f"• <a href='{link}'>{key}</a> - {summary}\n"
        report += "\n"

    if failed_stories:
        report += f"❌ <b>فشل تحليلهم ({len(failed_stories)})</b>\n"
        for key, summary, link in failed_stories:
            report += f"• <a href='{link}'>{key}</a> - {summary}\n"

    if regression_story:
        report += f"\n🔄 <b>Regression:</b> <a href='{JIRA_SERVER}browse/{regression_story}'>{regression_story}</a>\n"

    report += f"\n━━━━━━━━━━━━━━━━\n"
    report += f"📈 الإجمالي: {total} Stories"

    send_telegram(report)
    print(f"\nDONE: {project_name}")

# --- [Projects] ---
PROJECTS = [
    {
        "name": "Shine Laundries",
        "jql": 'project = "Shine Laundries" AND Sprint = 10 AND type = Story',
        "regression_story": None
    },
    {
        "name": "Project 2",
        "jql": 'project = "Project 2" AND Sprint = 1 AND type = Story',
        "regression_story": None
    },
]

# --- [التشغيل] ---
for project in PROJECTS:
    print(f"\n{'='*50}")
    print(f"Starting Project: {project['name']}")
    print(f"{'='*50}")
    run_jql(
        jql_query=project["jql"],
        project_name=project["name"],
        regression_story=project["regression_story"]
    )