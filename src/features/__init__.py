# Importing each handlers module registers its commands, callbacks and jobs with core.router.
from features.laundry import handlers as _laundry  # noqa: F401
from features.profile import handlers as _profile  # noqa: F401
from features.auth import handlers as _auth  # noqa: F401
from features.split import handlers as _split  # noqa: F401
from features.split import bill_actions as _split_bill_actions  # noqa: F401
from features.split import payments as _split_payments  # noqa: F401
from features.split import reminders as _split_reminders  # noqa: F401
