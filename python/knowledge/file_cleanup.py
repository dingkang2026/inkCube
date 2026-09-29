"""python/knowledge/file_cleanup.py

Shared cleanup logic for file deletion and retry.
Cleans: ChromaDB chunks + FTS5 chunks + image records + Wiki data + disk files.
"""
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


async def cleanup_file_data(
    db,
    vector_store,
    fts_store,
    file_id: str,
    project_id: str,
    data_dir: str,
    remove_file_record: bool = False,
):
    """
    Clean up all data associated with a file.

    Args:
        db: Database instance
        vector_store: VectorStore instance
        fts_store: FTSStore instance
        file_id: File ID to clean up
        project_id: Project ID
        data_dir: Data directory root
        remove_file_record: If True, also delete the file record from DB
                           and original file from disk. If False, only
                           clean derived data (chunks, images) for retry.
    """
    # 1. Delete ChromaDB chunks for this file
    try:
        vector_store.delete_by_file_id(project_id, file_id)
    except Exception as e:
        logger.warning("ChromaDB cleanup failed for %s: %s", file_id, e)

    # 2. Delete FTS5 chunks for this file
    try:
        fts_store.delete_by_file_id(file_id)
    except Exception as e:
        logger.warning("FTS cleanup failed for %s: %s", file_id, e)

    # 3. Delete image records and clean up extracted image files
    try:
        image_paths = db.delete_images_by_file(file_id)
        for img_path in image_paths:
            p = Path(img_path)
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Image cleanup failed for %s: %s", file_id, e)

    # 4. Clean Wiki-derived data (source_pages, page_sources, review refs)
    if remove_file_record:
        try:
            wiki_counts = db.cleanup_wiki_data_for_file(project_id, file_id)
            if any(v > 0 for v in wiki_counts.values()):
                logger.info("Wiki cleanup for %s: %s", file_id, wiki_counts)
        except Exception as e:
            logger.warning("Wiki cleanup failed for %s: %s", file_id, e)

    # 5. If full removal: delete original file from disk + DB record
    if remove_file_record:
        file_dir = Path(data_dir) / "files" / project_id / file_id
        if file_dir.exists():
            try:
                shutil.rmtree(file_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Disk cleanup failed for %s: %s", file_id, e)

        try:
            db.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            db.conn.commit()
        except Exception as e:
            logger.warning("DB record cleanup failed for %s: %s", file_id, e)
