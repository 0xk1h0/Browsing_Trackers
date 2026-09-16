===================================
Browser Privacy User Study
===================================

Thank you for participating! Estimated time: 30-40 minutes.

Requirements
------------
1. Python 3.8+              (check: python3 --version)
2. mitmproxy                (install: pip install mitmproxy)
3. Google Chrome / Chromium (usually already installed)

How to run
----------
1. Open a terminal (command prompt) and move into this folder:
   cd client

2. Start the client (use the participant ID you were given):
   python3 study_client.py --participant P001

3. For each task a browser window opens automatically:
   - Perform the task as you would normally
   - When done, switch back to the terminal and press Enter
   - Press 's' to skip a task, 'q' to quit the session

4. After all tasks are complete a ZIP file is created.
   Please send that ZIP file to the researcher.

If you had to stop mid-session (resume)
---------------------------------------
python3 study_client.py --participant P001 --start-from 5
(resumes from task #5)

Troubleshooting
---------------
- "Chrome not found": install Chrome -> https://www.google.com/chrome/
- "mitmdump not found": pip install mitmproxy
- Any other problem: contact the researcher

Contact
-------
[researcher email here]
