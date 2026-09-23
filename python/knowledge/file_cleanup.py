"""python/knowledge/file_cleanup.py

Shared cleanup logic for file deletion and retry.
Cleans: ChromaDB chunks + FTS5 chunks + image records + disk files.
"""
import shutil
from pathlib import Path


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
        print(f"[WARN] ChromaDB cleanup failed for {file_id}: {e}")

    # 2. Delete FTS5 chunks for this file
    try:
        fts_store.delete_by_file_id(file_id)
    except Exception as e:
        print(f"[WARN] FTS cleanup failed for {file_id}: {e}")

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
        print(f"[WARN] Image cleanup failed for {file_id}: {e}")

    # 4. If full removal: delete original file from disk + DB record
    if remove_file_record:
        file_dir = Path(data_dir) / "files" / project_id / file_id
        if file_dir.exists():
            try:
                shutil.rmtree(file_dir, ignore_errors=True)
            except Exception as e:
                print(f"[WARN] Disk cleanup failed for {file_id}: {e}")

        try:
            db.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            db.conn.commit()
        except Exception as e:
            print(f"[WARN] DB record cleanup failed for {file_id}: {e}")
