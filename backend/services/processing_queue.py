import asyncio
from typing import Optional

from backend.database import DocumentRepository, ProcessingRunRepository

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 30.0


async def _heartbeat(run_id: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            ProcessingRunRepository.heartbeat(run_id)
        except Exception as error:
            print(f"[Queue] Could not renew lease for run {run_id}: {error}")


async def processing_worker() -> None:
    """Poll the database queue; claimed work is recoverable after its lease expires."""
    try:
        ProcessingRunRepository.recover_orphans()
    except Exception as error:
        print(f"[Queue] Could not recover older processing documents: {error}")
    while True:
        try:
            run = ProcessingRunRepository.claim_next()
        except Exception as error:
            print(f"[Queue] Could not claim a run: {error}")
            await asyncio.sleep(5)
            continue
        if run is None:
            await asyncio.sleep(POLL_SECONDS)
            continue

        heartbeat_task = asyncio.create_task(_heartbeat(run["id"]))
        try:
            from backend.api.documents import process_and_dispatch

            error: Optional[str] = await process_and_dispatch(
                run["document_id"], run["file_path"], run["mime_type"], run["user_id"]
            )
            document = DocumentRepository.get_by_id(run["document_id"], user_id=run["user_id"])
            data = document.get("data") if document else None
            verification = (data or {}).get("verification") or {}
            flags = verification.get("flags") or []
            ProcessingRunRepository.finish(
                run["id"], "FAILED" if error else "SUCCEEDED", error=error,
                provider=(data or {}).get("extraction_provider"), model=(data or {}).get("extraction_model"),
                document_status=verification.get("status"),
                outcome="; ".join(flags[:4]) if flags else None,
            )
        except asyncio.CancelledError:
            ProcessingRunRepository.requeue(run["id"])
            raise
        except Exception as error:
            try:
                ProcessingRunRepository.finish(run["id"], "FAILED", error=str(error))
            except Exception as db_error:
                print(f"[Queue] Could not save failure for run {run['id']}: {db_error}")
            print(f"[Queue] Run {run['id']} failed: {error}")
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
