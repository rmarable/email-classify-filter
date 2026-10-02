from datetime import datetime

import pytest

from ecf_server.mail import MailSource
from ecf_server.mail.fake import FakeMailSource
from tests.mail_contract import Harness, MailSourceContract, message


class FakeHarness:
    def __init__(self) -> None:
        self.fake = FakeMailSource(uidvalidity=7)
        self.source: MailSource = self.fake
        self.move_target = "Archive"

    def deliver(self, raw: bytes, when: datetime) -> None:
        self.fake.deliver(raw, when)

    def expunge(self, uid: int) -> None:
        self.fake.expunge(uid)


class TestFakeContract(MailSourceContract):
    @pytest.fixture
    def harness(self) -> Harness:
        return FakeHarness()


def test_reset_renumbers_under_a_new_uidvalidity() -> None:
    f = FakeMailSource(uidvalidity=7)
    f.deliver(message(0))
    f.deliver(message(1))
    f.expunge(1)
    f.reset(8)
    assert f.inbox().uidvalidity == 8
    assert f.uids_after(0) == [1]
    assert f.fetch(1) == message(1)
