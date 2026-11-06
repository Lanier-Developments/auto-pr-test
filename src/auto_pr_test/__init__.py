"""auto-pr-test: a policy-as-code pull-request gate.

Three things a merge should be able to prove afterwards:

  1. the tests ran, passed, and coverage did not quietly fall;
  2. reviewers from more than one model family independently agreed, and the
     policy that let them read the code is on record;
  3. the record has not been altered since.

Everything else in this package exists to make those three provable.
"""

__version__ = "0.1.0"
