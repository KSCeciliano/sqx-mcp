from sq_mcp.tools._common import is_status_only_response, parse_list


def test_project_list_status_only_response_detected() -> None:
    text = (
        "04:35:09 List of available projects\n"
        "--------------------------------------------------\n"
        "Projects listed.\n"
        "--------------------------------------------------"
    )
    assert parse_list(text) == [
        "04:35:09 List of available projects",
        "Projects listed.",
    ]
    assert is_status_only_response(text) is True


def test_databank_count_status_only_response_detected() -> None:
    text = (
        "04:35:13 Number of strategies\n"
        "--------------------------------------------------\n"
        "Syncing databank(s) from files\n"
        "Synchronization finished.\n"
        "--------------------------------------------------\n"
        "Databank count retrieved.\n"
        "--------------------------------------------------"
    )
    filtered = parse_list(text)
    assert filtered == [
        "04:35:13 Number of strategies",
        "Databank count retrieved.",
    ]
    assert is_status_only_response(text) is True
