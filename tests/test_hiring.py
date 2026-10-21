import tempfile
import unittest
from pathlib import Path

import app as app_module
import database
from werkzeug.security import check_password_hash, generate_password_hash


class HiringSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database = database.DATABASE
        database.DATABASE = str(Path(self.temp_dir.name) / "hiring_test.db")
        database.create_tables()
        conn = database.get_connection()
        conn.execute("DELETE FROM users")
        conn.executemany(
            "INSERT INTO users (username, password_hash, full_name, role, system) VALUES (?, ?, ?, ?, ?)",
            [
                ("dist_admin", generate_password_hash("dist-password"), "Distribution Admin", "admin", "distribution"),
                ("hiring_admin", generate_password_hash("hiring-password"), "Hiring Admin", "admin", "hiring"),
                ("hiring_viewer", generate_password_hash("viewer-password"), "Hiring Viewer", "viewer", "hiring"),
            ],
        )
        conn.commit()
        conn.close()
        self.previous_testing = app_module.app.config.get("TESTING")
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def tearDown(self):
        database.DATABASE = self.previous_database
        app_module.app.config.update(TESTING=self.previous_testing)
        self.temp_dir.cleanup()

    def login_as(self, username, system):
        conn = database.get_connection()
        user = conn.execute("SELECT id, role FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        with self.client.session_transaction() as session:
            session["user_id"] = user["id"]
            session["username"] = username
            session["full_name"] = username
            session["role"] = user["role"]
            session["system"] = system

    def test_candidate_pipeline_and_role_boundaries(self):
        chooser = self.client.get("/login")
        self.assertIn(b"/login?system=hiring", chooser.data)

        self.login_as("dist_admin", "distribution")
        response = self.client.get("/hiring")
        self.assertEqual(response.status_code, 302)
        self.assertIn("system=hiring", response.headers["Location"])

        response = self.client.post(
            "/login?system=hiring",
            data={"system": "hiring", "username": "hiring_admin", "password": "hiring-password"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/hiring"))
        self.assertEqual(self.client.get("/hiring/candidates/new").status_code, 200)
        add_user_form = self.client.get("/users/add")
        self.assertIn(b"hiring_manager", add_user_form.data)
        self.assertIn(b"recruiter", add_user_form.data)

        response = self.client.post(
            "/hiring/candidates/new",
            data={
                "full_name": "Alex Candidate",
                "position": "Recruiter",
                "email": "alex@example.com",
                "status": "screening",
                "rating": "4",
            },
        )
        self.assertEqual(response.status_code, 302)
        pipeline = self.client.get("/hiring?q=Alex&status=screening")
        self.assertIn(b"Alex Candidate", pipeline.data)

        conn = database.get_connection()
        candidate_id = conn.execute("SELECT id FROM hiring_candidates").fetchone()["id"]
        conn.close()
        self.assertEqual(self.client.get(f"/hiring/candidates/{candidate_id}").status_code, 200)
        response = self.client.post(
            f"/hiring/candidates/{candidate_id}/status",
            data={"status": "interview"},
        )
        self.assertEqual(response.status_code, 302)

        self.login_as("hiring_viewer", "hiring")
        self.assertEqual(
            self.client.post("/hiring/candidates/new", data={"full_name": "Nope", "position": "Nope"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(f"/hiring/candidates/{candidate_id}/status", data={"status": "hired"}).status_code,
            403,
        )

    def test_candidate_search_and_position_order_are_case_insensitive(self):
        conn = database.get_connection()
        conn.executemany(
            """INSERT INTO hiring_candidates (full_name, position, status)
               VALUES (?, ?, ?)""",
            [
                ("Lowercase Candidate", "alpha", "new"),
                ("Uppercase Candidate", "Beta", "new"),
                ("Mixed Candidate", "ALPHA", "new"),
            ],
        )
        conn.commit()
        conn.close()

        self.login_as("hiring_admin", "hiring")
        response = self.client.get("/hiring?q=lowercase")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Lowercase Candidate", response.data)

        response = self.client.get("/hiring")
        positions = [
            response.data.index(b'value="ALPHA"'),
            response.data.index(b'value="alpha"'),
            response.data.index(b'value="Beta"'),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_hiring_administrator_setup_is_one_time(self):
        conn = database.get_connection()
        conn.execute("DELETE FROM users WHERE system = 'hiring'")
        conn.commit()
        conn.close()
        self.login_as("dist_admin", "distribution")

        self.assertEqual(self.client.get("/users/setup-hiring").status_code, 200)
        response = self.client.post(
            "/users/setup-hiring",
            data={"username": "first_hiring_admin", "full_name": "Hiring Admin", "password": "secure-pass-9"},
        )
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        user = conn.execute("SELECT * FROM users WHERE username = ?", ("first_hiring_admin",)).fetchone()
        conn.close()
        self.assertEqual(user["system"], "hiring")
        self.assertEqual(user["role"], "admin")
        self.assertTrue(check_password_hash(user["password_hash"], "secure-pass-9"))
        self.assertEqual(self.client.get("/users/setup-hiring").status_code, 302)

    def test_public_linkedin_application_enters_job_pipeline(self):
        public_client = app_module.app.test_client()
        self.assertEqual(public_client.get("/careers").status_code, 200)
        self.assertEqual(public_client.get("/hiring/jobs").status_code, 302)

        self.login_as("hiring_admin", "hiring")
        self.assertEqual(self.client.get("/hiring/jobs/new").status_code, 200)
        response = self.client.post(
            "/hiring/jobs/new",
            data={
                "title": "Recruiter",
                "location": "Remote",
                "employment_type": "Full-time",
                "description": "Build our team.",
                "status": "open",
            },
        )
        self.assertEqual(response.status_code, 302)

        job_page = self.client.get("/hiring/jobs")
        self.assertIn(b"LinkedIn application URL", job_page.data)
        self.assertIn(b"source=linkedin", job_page.data)
        conn = database.get_connection()
        job = conn.execute("SELECT id FROM hiring_jobs WHERE title = ?", ("Recruiter",)).fetchone()
        conn.close()
        self.assertEqual(self.client.get(f"/hiring/jobs/{job['id']}/edit").status_code, 200)
        application_url = f"/careers/{job['id']}/apply?source=linkedin"

        response = public_client.post(
            application_url,
            data={
                "full_name": "Public Applicant",
                "email": "applicant@example.com",
                "phone": "1234567890",
                "resume_url": "https://example.com/resume",
                "cover_note": "I would like to join the team.",
                "privacy_consent": "yes",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(public_client.get(response.headers["Location"]).status_code, 200)
        self.assertEqual(public_client.get("/hiring").status_code, 302)

        conn = database.get_connection()
        candidate = conn.execute(
            "SELECT full_name, email, position, source, status, job_id, created_by FROM hiring_candidates"
        ).fetchone()
        conn.close()
        self.assertEqual(
            tuple(candidate),
            ("Public Applicant", "applicant@example.com", "Recruiter", "LinkedIn", "new", job["id"], None),
        )

    def test_existing_candidates_migrate_for_public_applications(self):
        conn = database.get_connection()
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'hiring_admin'").fetchone()["id"]
        conn.execute("DROP TABLE hiring_candidates")
        conn.execute(
            """CREATE TABLE hiring_candidates (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               full_name TEXT NOT NULL,
               email TEXT,
               phone TEXT,
               position TEXT NOT NULL,
               source TEXT,
               status TEXT NOT NULL DEFAULT 'new',
               rating INTEGER,
               resume_url TEXT,
               notes TEXT,
               created_by INTEGER NOT NULL,
               created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
               updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (created_by) REFERENCES users(id)
           )"""
        )
        conn.execute(
            "INSERT INTO hiring_candidates (full_name, position, created_by) VALUES (?, ?, ?)",
            ("Existing Candidate", "Analyst", admin_id),
        )
        conn.commit()
        conn.close()

        database.create_tables()

        conn = database.get_connection()
        candidate = conn.execute(
            "SELECT full_name, job_id, created_by FROM hiring_candidates WHERE full_name = ?",
            ("Existing Candidate",),
        ).fetchone()
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(hiring_candidates)")}
        conn.close()
        self.assertEqual(tuple(candidate), ("Existing Candidate", None, admin_id))
        self.assertEqual(columns["created_by"]["notnull"], 0)
        self.assertIn("job_id", columns)


if __name__ == "__main__":
    unittest.main()