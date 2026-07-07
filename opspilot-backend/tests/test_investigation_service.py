import json
from unittest.mock import MagicMock, patch

from app.services import investigation_service


def _fake_paginator(pages: list[dict]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    return paginator


def _fake_embed_response(values: list[float]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"embedding": {"values": values}}
    response.raise_for_status.return_value = None
    return response


@patch("app.services.investigation_service.get_settings")
@patch("app.services.investigation_service.httpx.post")
@patch("app.services.investigation_service.get_dynamodb_client")
def test_save_investigation_puts_item_with_embedding(
    mock_get_client: MagicMock, mock_post: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(
        gemini_api_key="fake-key",
        gemini_embedding_model="gemini-embedding-001",
        opspilot_investigations_table="opspilot-investigations",
    )
    mock_post.return_value = _fake_embed_response([0.1, 0.2, 0.3])
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    result = investigation_service.save_investigation(
        question="Is anything wrong with my instance?",
        trace_summary="Checked CPU, checked status checks.",
        conclusion="Nothing wrong — CPU normal, status checks passed.",
    )

    mock_client.put_item.assert_called_once()
    call_kwargs = mock_client.put_item.call_args.kwargs
    assert call_kwargs["TableName"] == "opspilot-investigations"
    item = call_kwargs["Item"]
    assert item["id"]["S"] == result.id
    assert item["question"]["S"] == "Is anything wrong with my instance?"
    assert json.loads(item["embedding"]["S"]) == [0.1, 0.2, 0.3]


@patch("app.services.investigation_service.get_settings")
@patch("app.services.investigation_service.httpx.post")
@patch("app.services.investigation_service.get_dynamodb_client")
def test_find_similar_past_investigations_ranks_by_similarity(
    mock_get_client: MagicMock, mock_post: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(
        gemini_api_key="fake-key",
        gemini_embedding_model="gemini-embedding-001",
        opspilot_investigations_table="opspilot-investigations",
    )
    mock_post.return_value = _fake_embed_response([1.0, 0.0])

    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [
            {
                "Items": [
                    {
                        "id": {"S": "close-match"},
                        "question": {"S": "Q1"},
                        "trace_summary": {"S": "T1"},
                        "conclusion": {"S": "C1"},
                        "created_at": {"S": "2026-07-01T00:00:00Z"},
                        "embedding": {"S": json.dumps([0.99, 0.01])},
                    },
                    {
                        "id": {"S": "orthogonal"},
                        "question": {"S": "Q2"},
                        "trace_summary": {"S": "T2"},
                        "conclusion": {"S": "C2"},
                        "created_at": {"S": "2026-07-02T00:00:00Z"},
                        "embedding": {"S": json.dumps([0.0, 1.0])},
                    },
                ]
            }
        ]
    )
    mock_get_client.return_value = mock_client

    results = investigation_service.find_similar_past_investigations("some query", top_k=3)

    assert [r.id for r in results] == ["close-match", "orthogonal"]
    assert results[0].similarity > results[1].similarity


@patch("app.services.investigation_service.get_settings")
@patch("app.services.investigation_service.httpx.post")
@patch("app.services.investigation_service.get_dynamodb_client")
def test_find_similar_past_investigations_skips_items_without_embedding(
    mock_get_client: MagicMock, mock_post: MagicMock, mock_get_settings: MagicMock
) -> None:
    mock_get_settings.return_value = MagicMock(
        gemini_api_key="fake-key",
        gemini_embedding_model="gemini-embedding-001",
        opspilot_investigations_table="opspilot-investigations",
    )
    mock_post.return_value = _fake_embed_response([1.0, 0.0])

    mock_client = MagicMock()
    mock_client.get_paginator.return_value = _fake_paginator(
        [{"Items": [{"id": {"S": "no-embedding"}, "question": {"S": "Q"}}]}]
    )
    mock_get_client.return_value = mock_client

    results = investigation_service.find_similar_past_investigations("some query")

    assert results == []


@patch("app.services.investigation_service.get_settings")
def test_embed_raises_without_gemini_key(mock_get_settings: MagicMock) -> None:
    mock_get_settings.return_value = MagicMock(gemini_api_key=None)

    try:
        investigation_service._embed("text")
        raised = False
    except RuntimeError:
        raised = True

    assert raised
