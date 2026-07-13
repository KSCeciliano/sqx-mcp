from sq_mcp.tools import _common as common_mod


def test_databank_list_status_lines_can_be_filtered_against_filesystem_names():
    engine_lines = [
        '08:30:46 List of available databanks',
        'Loaded 0 strategies to databank Results',
        'Loaded 0 strategies to databank phase3_samples',
        'Loaded 0 strategies to databank phase3_out',
        'Databanks listed.',
    ]
    fs_names = {'Results', 'phase3_samples', 'phase3_out'}
    parsed_names = [s for s in engine_lines if s in fs_names]
    assert parsed_names == []


def test_status_only_count_response_is_detected():
    text = (
        '04:35:13 Number of strategies\n'
        '--------------------------------------------------\n'
        'Databank count retrieved.\n'
        '--------------------------------------------------'
    )
    assert common_mod.is_status_only_response(text) is True
