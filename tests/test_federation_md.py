from django.urls import reverse


def test_federation_md_is_served(client):
    response = client.get(reverse("federation-md"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/markdown")
    body = response.content.decode()
    assert "FEP-67ff" in body
    assert "NodeInfo" in body
