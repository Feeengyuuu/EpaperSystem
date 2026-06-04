# Magazine Covers Daily Pool Full Swap

For `magazine_covers` triptych mode, each refresh should consume three source IDs at once so the next refresh swaps all three covers instead of sliding forward by one cover.

The plugin should keep display choices inside the current daily library pool. Store a `daily_library_day_key` with the pool state and rebuild the library when the local day changes, even if the 23-hour refresh interval has not elapsed.
