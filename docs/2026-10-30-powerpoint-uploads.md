# PowerPoint uploads

Release 0.16.0 adds `canvas_upload_powerpoint` for authorized course files. It preserves the image uploader and introduces a separate trusted folder, exact confirmation, bounded OOXML validation and immutable payload. Canvas completion uses authenticated GET; course-scoped verification returns a stable direct download URL. Page publication is outside this tool's scope.

Tests mock credentials and network boundaries. Coverage includes valid packages, rejected extensions/macros/active content, root/path/size controls, disabled policy, wrong course and confirmation, immutable multipart data, 201 and redirect completion, verification mismatch, uncertain outcomes without retries, completion-origin checks and mutating tool annotations. Run the full unittest suite and compile checks before shipping.

Installation requires restarting or reconnecting existing MCP clients to discover the new tool. Configure `CANVAS_POWERPOINT_UPLOAD_ROOT` locally; do not commit private paths or enabled policies.
