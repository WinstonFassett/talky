# talky conformance report

Run against the deployed app on 2026-10-18, floor only.

pass 49 · fail 0 · n/a 7 · untested 0

| Scenario | Tags | Result | Detail |
| --- | --- | --- | --- |
| Following the stream while at the bottom | @must | pass | 20 updates, every one pinned on the next frame, scroll-behavior auto |
| Reading history while the agent streams | @must | pass | 20 updates (thinking, tool rows, text), scrollTop held at 2115 |
| A scroll-wheel up releases the pin at once | @must | pass | after one wheel tick the next update left the view 379px from the bottom |
| Scrolling up during a real reply is never pulled back | @must | pass | 7 renders while scrolled up left scrollTop at 2517; following resumed 40px from the bottom |
| A way back to the newest message | @must | pass | "Scroll to latest, 1 new" showed 1 unread above the composer and jumped to the bottom |
| Opening a conversation lands at the bottom | @must | pass | every painted frame with messages (17) was at the bottom |
| An existing conversation never flashes the empty state | @must | pass | the address showed a loading state, then the messages, and never the empty state |
| My own message always pins | @must | pass |  |
| Enter sends, Shift+Enter breaks a line, on desktop | @must | pass | Shift+Enter inserted a newline; Enter sent both lines and cleared the composer |
| Enter during IME composition never sends | @must | pass | a composing Enter (isComposing, keyCode 229) sent nothing in 2s; a plain Enter then sent in 6001ms |
| On a phone, Enter is a newline | @must | pass | Return inserted a newline; the Send button sent |
| The shortcut is written down | @must | pass | Enter to send, Shift + Enter for new line |
| The composer is focused and ready on load | @must | pass |  |
| The composer grows with the text, up to a limit | @must | pass | 36px → 76px → capped at 160px, scrolling inside |
| Send is disabled for empty input, and says why | @must | pass |  |
| A send that cannot go out keeps the draft | @must | pass | with the daemon down, Send kept the text and said why |
| The draft survives a reload or a deploy | @must | pass | drafts came back after a reload, in a new chat and in a saved conversation; one aged past an hour was dropped. Talky has no attachments, so there are none to restore |
| Send becomes Stop | @must | pass |  |
| Esc stops the agent | @must | pass | cancelled at "Line 1 of 30"; nothing arrived in the 8s after |
| A thinking indicator appears only when it is needed | @must | pass | no indicator on a fast reply; a polite status on a slow one |
| Each tool call shows a live timer | @must @agent | pass | running 0s → 2s; finished at "✓ DONE build 3s" |
| The whole task shows a live timer | @must @agent | pass | "task 1m 03s", tab title "Working… · Talky" |
| A stuck tool row never runs forever | @must @agent | pass | ✗ NO RESULT fetch 2s |
| A turn that ends with nothing visible says so | @must | pass |  |
| Reasoning is folded | @must @agent | pass |  |
| A new chat appears in the list at once | @must | pass | listed under Today before the reply; URL is /?session=00f459e8-e0f6-41c6-bc44-faa61b7f801d |
| Each chat shows whether it is working | @must | pass |  |
| Each chat shows working, idle, or failed at a glance | @must @agent | pass | 422 rows with dots: working animated, failed red, idle green |
| The list is grouped by time and searchable | @must | pass | groups Today / Yesterday / Last 7 days / Older; search narrowed to one title in 420ms |
| Copy a message | @must | pass | the clipboard got the message's markdown |
| Copy a code block | @must | pass | "copy" → "copied" → "copy" |
| Retry, edit, and branch | @must | pass | Retry re-sent it, Edit put it back in the composer, Branch off opened a new chat with it in the composer |
| Code is highlighted without blocking the first token | @must | pass | markdown on the first render; 2 highlighter chunks fetched after page load, then the block upgraded |
| Reconnecting is shown, then gives up honestly | @must | pass | Reconnecting, then Offline with Retry (no spinner); reconnected on Retry once the daemon was back |
| A stale close cannot undo a good connection | @must | pass | ten seconds after an end-and-restart the call stayed connected and a turn went through |
| A backgrounded tab catches up when it returns | @must | pass | frozen and hidden while the reply finished and the daemon restarted; on return it reconnected and showed the reply |
| A turn interrupted by a restart resumes | @must @agent | pass | after a daemon restart mid tool call the transcript said so and the turn ran to its reply |
| Errors sit in the transcript in order | @must | pass | the notice sat after the turn it belongs to and cleared on the next turn |
| A long error is summarised, with detail on demand | @must | pass | one line, the full traceback behind Details |
| A side panel can be hidden and shown again | @must | pass | hid (conversation 984px → 1280px) and came back at 296px |
| A side panel can be resized on desktop | @must | pass | follows the pointer (256 → 316px), stops at 200–480px, conversation keeps 800px |
| Panel sizes and visibility survive a reload | @must | pass | 270px survived a reload, and so did hidden |
| What is inside a panel adapts to its width | @must | pass | no sideways overflow at panel widths 200 and 480px, or at 900 and 700px windows |
| On a phone, side panels open over the conversation | @must | pass | the sessions sheet opens over the conversation from a button and closes again; no drag edge |
| A signed-out visitor sees one way in | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| The API refuses a caller who is not signed in | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| The first sign-in asks only what it needs, then lands somewhere useful | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| A cancelled or failed sign-in says why | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| Who I am is always one click away | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| Signing out is one visible control | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| After signing out, nothing private is reachable | @must | n/a | Talky has no accounts and no sign-in: it is a single-user app the owner runs on their own machine. The local listener binds loopback; the optional remote listener is HTTPS on the owner's network. There is no identity to sign in or out of. (The remote listener has no access control at all — tracked in WIN-555.) |
| The transcript is a polite live region | @must | pass | role=log aria-live=polite, heading "Conversation" |
| Motion respects the system setting | @must | pass | with reduced motion on, nothing animated while thinking, while a tool ran, or while text streamed |
| Every icon button has a name | @must | pass | 461 icon-only buttons, all named |
| No text sits on top of other text | @must | pass | no overlapping text on desktop and phone, idle and mid-turn |
| The empty state offers starters | @must | pass | "What do you want to talk about?" with 4 starters; one click sent "What can you help me with?" |
