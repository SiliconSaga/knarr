Feature: Config reconciliation

  Background:
    Given a Matrix homeserver is running
    And a valid config with test rooms

  Scenario: Validate accepts valid config
    When I run config validate
    Then it succeeds with "Config valid"

  Scenario: Audit reports drift for new rooms
    When I run config audit
    Then it reports "create" actions
    And it exits with code 2

  Scenario: Apply creates rooms from config
    When I run config apply
    Then room "#social-watch:knarr.local" exists in Matrix
    And room "#feed-reddit:knarr.local" exists in Matrix

  Scenario: Apply is idempotent
    Given the config has been applied
    When I run config audit
    Then it reports zero drift
    And it exits with code 0
