Feature: Config reconciliation

  Background:
    Given a Matrix homeserver is running
    And a valid config with test rooms

  Scenario: Validate accepts valid config
    When I run config validate
    Then it succeeds with "Config valid"

  # Single ordered scenario — the previous split into separate
  # audit/apply/idempotency scenarios was state-dependent against the shared
  # k3d homeserver and would race depending on prior test runs.
  Scenario: End-to-end reconcile lifecycle
    Given the configured rooms do not yet exist on the homeserver
    When I run config audit
    Then it reports "create" actions
    And it exits with code 2
    When I run config apply
    Then the configured rooms exist in Matrix
    When I run config audit
    Then it reports zero topology drift
