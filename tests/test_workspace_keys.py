"""Every API key covers its whole workspace, and only a signed-in person makes one
(Forrest 2026-09-27). The operator endpoint (POST/GET /api/v1/tokens, GET
/api/v1/users, behind DEXIO_ADMIN_TOKEN), the DEXIO_PUSH_TOKEN startup key, the
`dexio token` command and keys limited to one wiki are gone."""
import inspect

import pytest

from dexio import cli
from dexio.server import db


@pytest.mark.sqlite_only
def test_keys_once_limited_to_one_wiki_now_cover_the_workspace(tmp_path):
    path = str(tmp_path / "t.db")
    conn = db.connect(path)
    ws = db.default_workspace(conn)
    tok = db.create_token(conn, "legacy", workspace_id=ws)
    # A row as the operator endpoint used to write it, limited to one wiki.
    conn.execute("UPDATE tokens SET project='fleet' WHERE token_hash=?", (db.hash_token(tok),))
    conn.commit()
    conn.close()
    conn = db.connect(path)
    row = db.check_token(conn, tok)
    assert row and row["project"] is None and row["workspace_id"] == ws


def test_no_way_to_make_a_key_for_one_wiki():
    assert "project" not in inspect.signature(db.create_token).parameters


def test_no_operator_command_for_keys(capsys):
    with pytest.raises(SystemExit):
        cli.main(["token", "x"])
    assert "invalid choice" in capsys.readouterr().err
