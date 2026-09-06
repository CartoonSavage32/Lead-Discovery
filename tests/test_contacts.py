from __future__ import annotations

from app.config import ContactConfig
from app.contacts.extract import candidate_links, extract_contacts


HTML = """
<html>
  <body>
    <a href="/about">About</a>
    <a href="/contact">Contact</a>
    <p>Owner: Priya Sharma</p>
    <a href="mailto:hello@bakery.test">email</a>
    <a href="tel:+912212345678">phone</a>
    <script type="application/ld+json">
      {"@type":"Person","name":"Amit Patel","jobTitle":"Founder","email":"amit@bakery.test"}
    </script>
  </body>
</html>
"""


def test_extracts_labeled_roles_and_json_ld():
    contacts = extract_contacts(HTML, "https://bakery.test/about", ContactConfig())
    names = {item.name for item in contacts}
    assert "Priya Sharma" in names
    assert "Amit Patel" in names
    roles = {item.role.lower() for item in contacts if item.role}
    assert "owner" in roles
    assert "founder" in roles


def test_does_not_guess_unlabeled_names():
    html = "<p>Welcome to our shop. John likes bread. Contact us at info@shop.test</p>"
    contacts = extract_contacts(html, "https://shop.test/contact", ContactConfig())
    assert all(item.name is None or item.name != "John" for item in contacts)


def test_candidate_links_stay_on_site():
    links = candidate_links(HTML, "https://bakery.test/", ["about", "contact", "team"])
    assert "https://bakery.test/about" in links
    assert "https://bakery.test/contact" in links


def test_extract_skips_unusable_emails():
    html = '<a href="mailto:noreply@bakery.test">x</a><p>info@bakery.test</p>'
    contacts = extract_contacts(html, "https://bakery.test/contact", ContactConfig())
    emails = {item.email for item in contacts if item.email}
    assert "info@bakery.test" in emails
    assert "noreply@bakery.test" not in emails
