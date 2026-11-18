# Maestro development

## Keep browser tests out of live conversation storage

The service on port 8109 is Hubert's live Maestro. Browser tests previously
created 14 empty conversations in his sidebar while checking drafts and image
attachments. Do not repeat this.

- Run tests that create, fork, edit, send, upload, archive or delete against an
  isolated server with temporary state, or intercept all mutating API requests
  in the browser and provide in-memory fixtures before navigating.
- A fresh browser profile isolates localStorage, but does not isolate server
  state. Clicking New Chat on port 8109 creates a real conversation.
- Live visual checks must be read-only. Never send a model request as a test.
- Clean up only artifacts positively identified as belonging to your test;
  an empty conversation or a timestamp alone is not proof of ownership.

Do not restart maestro-web; other conversations may have active requests.
