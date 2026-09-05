"""The r23 API rejects mutations once the stack is frozen as read-only history."""

from fastapi import status


async def test_r23_mutations_return_read_only(client):
    response = await client.post(
        "/api/r23/runner-configs",
        json={"name": "new runner", "model": "model-x"},
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"]["code"] == "read_only"


async def test_r23_reads_still_work(client):
    response = await client.get("/api/r23/runner-configs")
    assert response.status_code == status.HTTP_200_OK
