# Visibility Test Data Builder

Internal web app for QA automation engineers to create and store expected UI visibility data for test scenarios.

Visibility is a boolean expectation only:

- `True` — the UI element must be visible
- `False` — the UI element must not be visible

## Stack

- Backend: Python FastAPI
- Server: Uvicorn
- UI: Jinja2 templates + static CSS/JavaScript
- Database: local SQLite (`app.db`, created automatically)
- Default port: `8000`

## Roles

| Role | Access |
|------|--------|
| **Admin** | Full app access + manage users |
| **Automation** | Full app access (Elements, Templates, Scenarios) |
| **Manual tester** | Templates + Scenarios (no Elements section) |

Default admin account (created on first startup if no users exist):

- Username: `user`
- Password: `Bojan1254`

## Features

- **Login / authorization** — session cookie auth with role-based access
- **Global element library** — store UI elements (`display_name`, unique `technical_key`, `group_name`) with search/filter
- **Templates** — reusable element sets that can be applied to a scenario without duplicating existing elements
- **Visibility scenarios** — named scenarios with True/False toggles per element, persisted in SQLite
- **Test data export** — read-only Python and JSON previews with copy buttons

## Windows setup and run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

## Project structure

```
visibility-test-data-builder/
├── main.py
├── requirements.txt
├── README.md
├── app.db                     # auto-created on startup
├── templates/
│   ├── index.html
│   └── login.html
└── static/
    ├── style.css
    └── app.js
```

## Example export

Python:

```python
expected_visibility = {
    "Close button": True,
    "Submit button": False,
}
```

JSON:

```json
{
  "Close button": true,
  "Submit button": false
}
```
