# talky conformance report

Run against the deployed app on 2026-10-18, floor only.

pass 30 · fail 19 · n/a 7 · untested 0

| Scenario | Tags | Result | Detail |
| --- | --- | --- | --- |
| Following the stream while at the bottom | @must | fail | a frame after new content sat 43px from the bottom |
| Reading history while the agent streams | @must | pass | 19 updates (thinking, tool rows, text), scrollTop held at 2115 |
| A scroll-wheel up releases the pin at once | @must | pass | after one wheel tick the next update left the view 379px from the bottom |
| Scrolling up during a real reply is never pulled back | @must | pass | 7 renders while scrolled up left scrollTop at 2492; following resumed 40px from the bottom |
| A way back to the newest message | @must | fail | the button shows 0 unread after a reply arrived |
| Opening a conversation lands at the bottom | @must | pass | every painted frame with messages (14) was at the bottom |
| An existing conversation never flashes the empty state | @must | fail | /?session=2f3f682f-e5f8-4879-91f5-5161fef2b157 did not show that conversation |
| My own message always pins | @must | pass |  |
| Enter sends, Shift+Enter breaks a line, on desktop | @must | fail | Shift+Enter sent the message |
| Enter during IME composition never sends | @must | fail | Enter while composing sent the message |
| On a phone, Enter is a newline | @must | pass | Return inserted a newline; the Send button sent |
| The shortcut is written down | @must | pass | Enter to send, Shift + Enter for new line |
| The composer is focused and ready on load | @must | pass |  |
| The composer grows with the text, up to a limit | @must | pass | 36px → 76px → capped at 160px, scrolling inside |
| Send is disabled for empty input, and says why | @must | pass |  |
| A send that cannot go out keeps the draft | @must | pass | with the daemon down, Send kept the text and said why |
| The draft survives a reload or a deploy | @must | pass | the draft came back after a reload; one aged past an hour was dropped. Talky has no attachments, so there are none to restore |
| Send becomes Stop | @must | pass |  |
| Esc stops the agent | @must | pass | cancelled at "Line 1 of 30" |
| A thinking indicator appears only when it is needed | @must | pass | no indicator on a fast reply; a polite status on a slow one |
| Each tool call shows a live timer | @must @agent | pass | running 0s → 2s; finished at "✓ DONE build 3s" |
| The whole task shows a live timer | @must @agent | pass | "task 1m 03s", tab title "Working… · Talky" |
| A stuck tool row never runs forever | @must @agent | pass | ✗ NO RESULT fetch 2s |
| A turn that ends with nothing visible says so | @must | pass |  |
| Reasoning is folded | @must @agent | fail | the reasoning is open while streaming |
| A new chat appears in the list at once | @must | fail | the chat was not listed under Today within 3s |
| Each chat shows whether it is working | @must | fail | the row has no 'Response in progress' status |
| Each chat shows working, idle, or failed at a glance | @must @agent | fail | locator.waitFor: Timeout 30000ms exceeded. |
| The list is grouped by time and searchable | @must | pass | groups Today / Yesterday / Last 7 days / Older; search narrowed to one title |
| Copy a message | @must | fail | page.waitForFunction: Timeout 10000ms exceeded. |
| Copy a code block | @must | fail | page.waitForFunction: Timeout 10000ms exceeded. |
| Retry, edit, and branch | @must | fail | Branch off did not carry the message into the new chat's composer |
| Code is highlighted without blocking the first token | @must | fail | the code block was never highlighted |
| Reconnecting is shown, then gives up honestly | @must | pass | Reconnecting, then Offline with Retry; Retry reconnected once the daemon was back |
| A stale close cannot undo a good connection | @must | pass | ten seconds after an end-and-restart the call stayed connected and a turn went through |
| A backgrounded tab catches up when it returns | @must | pass | frozen hidden for 15s while the turn finished; on return it was connected and showed the reply |
| A turn interrupted by a restart resumes | @must @agent | fail | the transcript never said the turn was interrupted |
| Errors sit in the transcript in order | @must | fail | locator.waitFor: Timeout 15000ms exceeded. |
| A long error is summarised, with detail on demand | @must | fail | locator.waitFor: Timeout 15000ms exceeded. |
| A side panel can be hidden and shown again | @must | pass | hid (conversation 984px → 1280px) and came back at 296px |
| A side panel can be resized on desktop | @must | pass | follows the pointer (296 → 356px), stops at 200–480px, conversation keeps 800px |
| Panel sizes and visibility survive a reload | @must | pass | 270px survived a reload, and so did hidden |
| What is inside a panel adapts to its width | @must | fail | at a 200px panel: div.absolute -right-1 top-0 z-20 h-full w-2  sticks out of aside; aside scrolls sideways |
| On a phone, side panels open over the conversation | @must | pass | the sessions sheet opens over the conversation from a button and closes again; no drag edge |
| A signed-out visitor sees one way in | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| The API refuses a caller who is not signed in | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| The first sign-in asks only what it needs, then lands somewhere useful | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| A cancelled or failed sign-in says why | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| Who I am is always one click away | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| Signing out is one visible control | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| After signing out, nothing private is reachable | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked as its own ticket.) |
| The transcript is a polite live region | @must | pass | role=log aria-live=polite, heading "Conversation" |
| Motion respects the system setting | @must | pass | with reduced motion on, nothing animated during a turn |
| Every icon button has a name | @must | pass | 110 icon-only buttons, all named |
| No text sits on top of other text | @must | fail | desktop, idle: "A very long session title that" overlaps "now"; "[[sleep 4]] [[say done.]]" overlaps "5m" |
| The empty state offers starters | @must | fail | page.waitForSelector: Timeout 5000ms exceeded. |
