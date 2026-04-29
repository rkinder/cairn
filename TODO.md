# TODO - Message Deletion API Implementation

- [x] Add DB migration(s) to support soft-delete metadata for messages in topic DBs and message_index.
- [x] Update base schema files to include soft-delete fields for new installs.
- [x] Add admin capability helper(s) in API dependencies.
- [x] Implement DELETE `/messages/{message_id}` (soft delete + admin-only hard delete).
- [x] Implement DELETE `/messages` bulk-by-tag with `confirm=true` safety.
- [x] Implement DELETE `/messages/thread/{thread_id}` purge endpoint.
- [x] Update message query/detail endpoints to handle deleted-record visibility rules.
- [x] Add/adjust tests for authorization, soft/hard delete behavior, bulk/thread deletion, and query visibility.
- [ ] Update documentation/spec references for the new endpoints and behavior.
- [x] Run targeted tests and summarize results.

## In Progress
- [x] Plan approved by user for all three endpoints.
- [x] Starting implementation edits now.
- [x] Implement DELETE endpoints in `cairn/api/routes/messages.py`.
- [x] Update message query/detail visibility behavior for soft-deleted records.
- [x] Add/update tests for delete behavior and authorization matrix.
- [x] Run thorough endpoint testing and summarize findings.

## Test Summary (Completed)
- [x] `pytest -q tests/test_skill_client.py -q`
- [x] `pytest -q tests/test_db_init_it.py tests/test_skill_client.py tests/test_promotions_methodology_kind.py tests/test_promotion_review.py -q`
- [x] `pytest -q tests/test_messages_delete_api.py -q`

## Remaining
- [ ] Documentation/spec update pass for message deletion endpoints/behavior.
