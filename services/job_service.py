import logging

from config.settings import Config

logger = logging.getLogger(__name__)


def enqueue_claim_processing(claim_id):
    """
    Optional RQ-based background hook. If Redis/RQ is not configured, callers
    can safely continue with synchronous processing.
    """
    if not getattr(Config, "CLAIM_PROCESSING_ASYNC", False):
        return {"queued": False, "reason": "async_disabled"}

    try:
        from redis import Redis
        from rq import Queue

        redis_url = getattr(Config, "REDIS_URL", "redis://localhost:6379/0")
        queue = Queue("claims", connection=Redis.from_url(redis_url))
        job = queue.enqueue("services.claim_processing_service.process_claim_job", claim_id)
        return {"queued": True, "job_id": job.id}
    except Exception as exc:
        logger.warning("[Jobs] Claim processing queue unavailable: %s", exc)
        return {"queued": False, "reason": str(exc)}
