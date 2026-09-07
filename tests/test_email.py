from __future__ import annotations

import time

import pytest

from app.contacts.email import domain_can_receive_mail, is_deliverable_email, is_usable_email


def test_rejects_organizational_association_addresses():
    assert is_usable_email("info@aerztekammer-berlin.de") is False
    assert is_usable_email("contact@vet-association.org") is False
    assert is_usable_email("hello@gmail.com") is True
    assert is_usable_email("owner@hotmail.com") is True
    assert is_usable_email("chambers@urvetcare.com") is True


@pytest.mark.real_dns
def test_mx_rejects_nonexistent_domain_and_accepts_mailbox_providers():
    assert domain_can_receive_mail("test@thisdomaindoesnotexist12345.com") is False
    assert domain_can_receive_mail("hello@gmail.com") is True
    assert domain_can_receive_mail("owner@hotmail.com") is True
    assert is_deliverable_email("test@thisdomaindoesnotexist12345.com") is False
    assert is_deliverable_email("hello@gmail.com") is True
    assert is_deliverable_email("info@aerztekammer-berlin.de") is False


@pytest.mark.real_dns
def test_mx_lookup_is_fast_with_timeout():
    started = time.perf_counter()
    domain_can_receive_mail("hello@gmail.com")
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0
