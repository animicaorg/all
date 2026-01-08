# Code Review Response

## Review Comment
> Using `asyncio.run()` within an exception handler could potentially interfere with any existing event loop. Consider checking if there's an existing event loop and using `await` instead, or use `asyncio.create_task()` if already in an async context.

## Response

### Context
The `list_snapshots()` function is a synchronous CLI command (regular `def`, not `async def`) decorated with `@app.command("list")`. It's called directly from the Typer CLI framework.

### Current Pattern in File
The file already uses `asyncio.run()` extensively throughout the same function:
- Line 267: `result = asyncio.run(rpc_call(...))`
- Line 333: `result = asyncio.run(rpc_call(...))`
- Line 397: `result = asyncio.run(rpc_call(...))`
- Line 418: `peer_result = asyncio.run(rpc_call(...))`
- **Line 439 (our change)**: `peers = asyncio.run(_get_peers(...))`

### Why This Is Correct
1. **CLI Context**: This is a CLI command entry point, not running in an async context
2. **Consistency**: Our implementation follows the established pattern in the file
3. **Typer Framework**: Typer commands are synchronous by default; async operations are wrapped in `asyncio.run()`
4. **No Event Loop**: When called from CLI, there's no existing event loop running

### Alternative Considered
If we wanted to make the entire function async:
```python
@app.command("list")
async def list_snapshots(...):
    # Use await directly
    peers = await _get_peers(url, timeout=timeout or 10.0)
```

However, this would require:
1. Making the entire function async
2. Changing all existing `asyncio.run()` calls to `await`
3. Ensuring Typer handles async commands (it does, but this would be a larger refactor)

### Decision
**Keep the current implementation** because:
- ✅ Follows existing patterns in the file
- ✅ Minimal, surgical fix
- ✅ No breaking changes
- ✅ Works correctly in CLI context
- ✅ Consistent with the rest of the codebase

### Future Improvement
If the entire module were to be refactored to use async/await throughout, this would be a good candidate for improvement. However, that's outside the scope of this bug fix.
