# Navbar update indicator modal design

## Goal
Replace the fragile hover-popover update action in the header with a safer interaction:
- hover shows status text only
- click opens a modal
- the modal contains the update action

## Scope
Only the navbar update indicator changes. The Settings page update flow stays as-is.

## Design
1. Keep the header indicator rendering from `partials/header_update_indicator.html`.
2. Change hover behavior so the user sees a tooltip-style status hint, not a clickable popover.
3. Add a click handler on the indicator that opens a modal overlay.
4. Render the upgrade action inside the modal, reusing the existing `/upgrade` route.
5. Preserve the current 2-minute refresh loop and the zero-egress guard when update checks are disabled.

## Testing
- verify the header no longer exposes the old hover actions
- verify clicking the indicator opens the modal
- verify the modal contains the upgrade button
- verify the update polling/egress behavior remains unchanged
