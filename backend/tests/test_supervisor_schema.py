from domain.archetypes.supervisor_agent import _Decomposition


def test_decomposition_schema_is_strict_for_openai_responses():
    schema = _Decomposition.model_json_schema()

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["_SubQueryAssignment"]["additionalProperties"] is False
