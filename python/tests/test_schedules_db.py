"""python/tests/test_schedules_db.py — 2.11 E3 T1: task_schedules / task_schedule_runs
两表与 db 方法单测（2.11 §3）。

覆盖：订阅 CRUD（含 update 白名单 / q·category·enabled 过滤 / latest_done 附加）、
到期扫描、run 生命周期（running→done/failed/missed）、收件箱未读优先、digest
每订阅最新一条、防重入查询、增量注入锚点（last_done_run_report / pending_archive_runs）、
保留期 workspace 清点、未读计数（摘要行数据源）、删除级联（runs CASCADE +
tasks.schedule_id SET NULL，外键 PRAGMA foreign_keys=ON）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from db.database import Database


@pytest.fixture
def tmp_db():
    database = Database(db_path=":memory:")
    database.initialize()
    yield database
    database.conn.close()


def _backdate(db, run_id, started_at=None, finished_at=None):
    """显式控制 run 时间戳（连续 insert/update 的 now 微秒序不稳定，排序断言需确定性）。"""
    sets, params = [], []
    if started_at is not None:
        sets.append("started_at = ?")
        params.append(started_at)
    if finished_at is not None:
        sets.append("finished_at = ?")
        params.append(finished_at)
    db.conn.execute(
        f"UPDATE task_schedule_runs SET {', '.join(sets)} WHERE id = ?",
        [*params, run_id])
    db.conn.commit()


# ── 订阅 CRUD ────────────────────────────────────────────────


def test_create_and_list_schedule(tmp_db):
    s = tmp_db.create_schedule('华润舆情', '扫描', 'general', 'corporate',
                               'weekly', 'MON 09:00',
                               next_run_at='2026-10-19T09:00:00')
    assert s['id'].startswith('sch_')
    rows = tmp_db.list_schedules()
    assert rows[0]['name'] == '华润舆情'
    assert rows[0]['next_run_at'] == '2026-10-19T09:00:00'
    assert rows[0]['enabled'] == 1 and rows[0]['auto_import'] == 1
    assert rows[0]['latest_done'] is None          # 尚无产出


def test_get_schedule_missing_returns_none(tmp_db):
    assert tmp_db.get_schedule('sch_nope') is None


def test_update_schedule_whitelist(tmp_db):
    s = tmp_db.create_schedule('a', 'p1', 'general', 'general', 'daily', '09:00')
    assert tmp_db.update_schedule(s['id'], {
        'name': 'b', 'prompt': 'p2', 'interval_type': 'weekly',
        'interval_value': 'MON,THU 09:00', 'enabled': 0, 'next_run_at': None,
        'archive_file_id': 'file_1', 'archive_last_run_id': 'run_9',
        'hacker_column': 'x',                      # 白名单外忽略
    })
    row = tmp_db.get_schedule(s['id'])
    assert row['name'] == 'b' and row['prompt'] == 'p2'
    assert row['interval_value'] == 'MON,THU 09:00'
    assert row['enabled'] == 0 and row['next_run_at'] is None
    # 档案锚点两列可写（T10 回归：白名单曾遗漏 archive_last_run_id，
    # 致合并后锚点静默不推进、rebuild 首批即止）
    assert row['archive_file_id'] == 'file_1'
    assert row['archive_last_run_id'] == 'run_9'
    assert 'hacker_column' not in row.keys()


def test_list_schedules_filters(tmp_db):
    tmp_db.create_schedule('华润舆情', 'p', 'general', 'corporate', 'daily', '09:00')
    tmp_db.create_schedule('产业周报', 'p', 'general', 'industry', 'daily', '09:00',
                           enabled=0)
    assert len(tmp_db.list_schedules(category='corporate')) == 1
    assert len(tmp_db.list_schedules(enabled=0)) == 1
    assert tmp_db.list_schedules(q='华润')[0]['name'] == '华润舆情'
    assert tmp_db.list_schedules(q='不存在') == []


def test_list_schedules_attaches_latest_done(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    tmp_db.insert_schedule_run('run_old', s['id'], None)
    tmp_db.update_schedule_run('run_old', 'done', summary=['旧'])
    _backdate(tmp_db, 'run_old', finished_at='2026-10-01T09:00:00')
    tmp_db.insert_schedule_run('run_new', s['id'], None)
    tmp_db.update_schedule_run('run_new', 'done', summary=['新1', '新2'])
    _backdate(tmp_db, 'run_new', finished_at='2026-10-02T09:00:00')
    latest = tmp_db.list_schedules()[0]['latest_done']
    assert latest['run_id'] == 'run_new'
    assert latest['finished_at'] == '2026-10-02T09:00:00'
    assert latest['summary'] == ['新1', '新2']     # JSON 解析为列表


def test_list_schedules_flags_running(tmp_db):
    """§8.4 行呼吸点数据源：is_running = 该订阅存在 running run。"""
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    s2 = tmp_db.create_schedule('b', 'p', 'general', 'general', 'daily', '09:00')
    tmp_db.insert_schedule_run('run_1', s['id'], None)     # 仍 running
    tmp_db.insert_schedule_run('run_2', s2['id'], None)
    tmp_db.update_schedule_run('run_2', 'done')
    rows = {r['name']: r for r in tmp_db.list_schedules()}
    assert rows['a']['is_running'] is True
    assert rows['b']['is_running'] is False


# ── 调度判定 ─────────────────────────────────────────────────


def test_due_filter(tmp_db):
    tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00',
                           next_run_at='2026-01-01T00:00:00', enabled=1)
    tmp_db.create_schedule('b', 'p', 'general', 'general', 'daily', '09:00',
                           next_run_at='2099-01-01T00:00:00', enabled=1)
    tmp_db.create_schedule('c', 'p', 'general', 'general', 'daily', '09:00',
                           next_run_at='2026-01-01T00:00:00', enabled=0)
    assert [r['name'] for r in tmp_db.list_due_schedules('2026-10-18T00:00:00')] == ['a']


def test_set_schedule_state(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00',
                               next_run_at='2026-01-01T00:00:00')
    tmp_db.set_schedule_state(s['id'], next_run_at='2026-01-08T09:00:00',
                              last_run_at='2026-01-01T09:00:00')
    row = tmp_db.get_schedule(s['id'])
    assert row['next_run_at'] == '2026-01-08T09:00:00'
    assert row['last_run_at'] == '2026-01-01T09:00:00'
    tmp_db.set_schedule_state(s['id'], consecutive_failures=2, enabled=0)
    row = tmp_db.get_schedule(s['id'])
    assert row['consecutive_failures'] == 2 and row['enabled'] == 0


# ── run 生命周期与阅读层 ─────────────────────────────────────


def test_run_lifecycle_and_inbox(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00',
                               next_run_at=None)
    r1 = 'run_a1'
    tmp_db.insert_schedule_run(r1, s['id'], None)
    tmp_db.update_schedule_run(r1, 'done', report_path='x.md', summary=['要点1'])
    _backdate(tmp_db, r1, finished_at='2026-10-01T09:00:00')
    r2 = 'run_a2'
    tmp_db.insert_schedule_run(r2, s['id'], None)
    tmp_db.update_schedule_run(r2, 'done')
    _backdate(tmp_db, r2, finished_at='2026-10-02T09:00:00')
    inbox = tmp_db.list_run_inbox()
    assert [r['id'] for r in inbox] == [r2, r1]           # 未读在前
    assert all(r['schedule_name'] == 'a' for r in inbox)
    assert tmp_db.mark_run_read(r2)
    assert [r['id'] for r in tmp_db.list_run_inbox()] == [r1, r2]  # 已读沉底
    # unread_only 过滤：r2 已读后仅剩 r1 未读
    assert [r['id'] for r in tmp_db.list_run_inbox(unread_only=True)] == [r1]


def test_mark_run_read_idempotent(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    tmp_db.insert_schedule_run('run_x', s['id'], None)
    assert tmp_db.mark_run_read('run_x')
    assert not tmp_db.mark_run_read('run_missing')         # 不存在 → False


def test_list_schedule_runs_desc_with_summary(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    tmp_db.insert_schedule_run('run_1', s['id'], None)
    tmp_db.update_schedule_run('run_1', 'done', summary=['k1'])
    _backdate(tmp_db, 'run_1', started_at='2026-10-01T09:00:00')
    tmp_db.insert_schedule_run('run_2', s['id'], None)
    _backdate(tmp_db, 'run_2', started_at='2026-10-02T09:00:00')
    runs = tmp_db.list_schedule_runs(s['id'])
    assert [r['id'] for r in runs] == ['run_2', 'run_1']   # started_at DESC
    assert runs[1]['summary'] == ['k1']
    assert tmp_db.list_schedule_runs(s['id'], limit=1)[0]['id'] == 'run_2'


def test_update_run_error_column(tmp_db):
    """§8.4 ReadView 失败态：update_schedule_run 记录失败原因（error 列）。"""
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    tmp_db.insert_schedule_run('run_f', s['id'], None)
    tmp_db.update_schedule_run('run_f', 'failed', error='report.md 未产出或为空')
    assert tmp_db.get_run('run_f')['error'] == 'report.md 未产出或为空'
    # 非失败终态不传 error → 不覆盖（列保持 NULL）
    tmp_db.insert_schedule_run('run_d', s['id'], None)
    tmp_db.update_schedule_run('run_d', 'done', summary=['k'])
    assert tmp_db.get_run('run_d')['error'] is None


def test_list_run_digest_latest_per_schedule(tmp_db):
    """T9 修订①：每订阅最新 1 条 run（含 running/failed）+ 订阅 enabled——
    §8.4 速览行三态（运行呼吸/失败红/暂停灰）数据可达。"""
    s1 = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    s2 = tmp_db.create_schedule('b', 'p', 'general', 'general', 'daily', '10:00')
    s3 = tmp_db.create_schedule('c', 'p', 'general', 'general', 'daily', '11:00')
    tmp_db.insert_schedule_run('run_a1', s1['id'], None)
    tmp_db.update_schedule_run('run_a1', 'done')
    _backdate(tmp_db, 'run_a1', finished_at='2026-10-01T09:00:00')
    tmp_db.insert_schedule_run('run_a2', s1['id'], None)
    tmp_db.update_schedule_run('run_a2', 'done')
    _backdate(tmp_db, 'run_a2', finished_at='2026-10-02T09:00:00')
    tmp_db.insert_schedule_run('run_b1', s2['id'], None)   # running 最新 → 进 digest（呼吸点）
    tmp_db.insert_schedule_run('run_c1', s3['id'], None)
    tmp_db.update_schedule_run('run_c1', 'failed', error='x')  # failed 最新 → 进 digest（异常红）
    digest = tmp_db.list_run_digest()
    assert [r['id'] for r in digest] == ['run_c1', 'run_b1', 'run_a2']
    by_id = {r['id']: r for r in digest}
    assert by_id['run_b1']['status'] == 'running'
    assert by_id['run_c1']['status'] == 'failed'
    assert by_id['run_a2']['schedule_enabled'] == 1
    # 订阅暂停 → 行带 schedule_enabled=0（速览灰显，§8.4 连续失败暂停）
    tmp_db.update_schedule(s1['id'], {'enabled': 0})
    after = {r['id']: r['schedule_enabled'] for r in tmp_db.list_run_digest()}
    assert after['run_a2'] == 0


def test_unread_count(tmp_db):
    s1 = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    s2 = tmp_db.create_schedule('b', 'p', 'general', 'general', 'daily', '09:00',
                                enabled=0)                     # 手动暂停（0 失败）
    s3 = tmp_db.create_schedule('c', 'p', 'general', 'general', 'daily', '09:00')
    tmp_db.set_schedule_state(s3['id'], enabled=0, consecutive_failures=3)
    tmp_db.insert_schedule_run('run_1', s1['id'], None)
    tmp_db.update_schedule_run('run_1', 'done')
    _backdate(tmp_db, 'run_1', finished_at='2026-10-01T09:00:00')
    tmp_db.insert_schedule_run('run_2', s1['id'], None)
    tmp_db.update_schedule_run('run_2', 'done')
    tmp_db.mark_run_read('run_2')
    counts = tmp_db.schedule_unread_count()
    # 未读1 / 熔断1（手动暂停不计入，2026-10-23 口径收紧）/ 共3
    assert counts == {'total': 1, 'abnormal': 1, 'n': 3}


# ── 调度器辅助查询 ───────────────────────────────────────────


def test_count_running_runs_and_last_run_running(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    assert tmp_db.count_running_runs() == 0
    assert not tmp_db.schedule_last_run_running(s['id'])
    tmp_db.insert_schedule_run('run_1', s['id'], None)     # 默认 running
    assert tmp_db.count_running_runs() == 1
    assert tmp_db.schedule_last_run_running(s['id'])
    tmp_db.insert_schedule_run('run_2', s['id'], None)
    tmp_db.update_schedule_run('run_2', 'done')
    # run_1 倒退到过去，run_2 保持真实时间（自然最新）——最新一条非 running
    _backdate(tmp_db, 'run_1', started_at='2026-10-01T09:00:00')
    assert not tmp_db.schedule_last_run_running(s['id'])


def test_last_done_run_report(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    assert tmp_db.last_done_run_report(s['id']) is None
    tmp_db.insert_schedule_run('run_1', s['id'], None)
    tmp_db.update_schedule_run('run_1', 'done', report_path='data/schedules/run_1.md')
    assert tmp_db.last_done_run_report(s['id']) == 'data/schedules/run_1.md'


def test_pending_archive_runs_anchor(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    for i, day in enumerate(['01', '02', '03'], 1):
        tmp_db.insert_schedule_run(f'run_{i}', s['id'], None)
        tmp_db.update_schedule_run(f'run_{i}', 'done')
        _backdate(tmp_db, f'run_{i}', started_at=f'2026-10-{day}T09:00:00')
    # 锚点空 = 全部 done（升序）
    assert [r['id'] for r in tmp_db.pending_archive_runs(s['id'])] == [
        'run_1', 'run_2', 'run_3']
    # 锚点 = run_1 → 之后 2 期
    tmp_db.conn.execute("UPDATE task_schedules SET archive_last_run_id = 'run_1'"
                        " WHERE id = ?", (s['id'],))
    tmp_db.conn.commit()
    assert [r['id'] for r in tmp_db.pending_archive_runs(s['id'])] == [
        'run_2', 'run_3']
    # 锚点 = 最新 → 空（已全部合并）
    tmp_db.conn.execute("UPDATE task_schedules SET archive_last_run_id = 'run_3'"
                        " WHERE id = ?", (s['id'],))
    tmp_db.conn.commit()
    assert tmp_db.pending_archive_runs(s['id']) == []


def test_count_schedule_workspaces_keep(tmp_db, tmp_path):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    task = tmp_db.create_task('影子任务', data_dir=str(tmp_path))
    tmp_db.conn.execute("UPDATE tasks SET workspace_path = ? WHERE id = ?",
                        (f"D:/data/tasks/{task['id']}/workspace", task['id']))
    tmp_db.conn.commit()
    tmp_db.insert_schedule_run('run_1', s['id'], task['id'])
    tmp_db.update_schedule_run('run_1', 'done')
    _backdate(tmp_db, 'run_1', started_at='2026-10-01T09:00:00')
    assert tmp_db.count_schedule_workspaces_keep(s['id'], keep=1) == []
    assert tmp_db.count_schedule_workspaces_keep(s['id'], keep=0) == [
        f"D:/data/tasks/{task['id']}/workspace"]


def test_count_workspaces_keep_includes_failed(tmp_db, tmp_path):
    """P2-8：保留期清点纳入 failed run——连续失败订阅的 workspace 同样
    按 started_at 统一排序回收，目录不永久累积。"""
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    paths = {}
    for i in range(1, 5):
        ws = tmp_path / f"ws_{i:02d}"
        ws.mkdir()
        task = tmp_db.create_task(f'影子{i}', data_dir=str(tmp_path))
        tmp_db.conn.execute(
            "UPDATE tasks SET workspace_path = ? WHERE id = ?",
            (str(ws), task['id']))
        run = f'run_{i:02d}'
        tmp_db.insert_schedule_run(run, s['id'], task['id'])
        tmp_db.update_schedule_run(run, 'done' if i <= 2 else 'failed')
        _backdate(tmp_db, run,
                  started_at=f'2026-10-{i:02d}T09:00:00')
        paths[run] = str(ws)
    stale = tmp_db.count_schedule_workspaces_keep(s['id'], keep=2)
    assert set(stale) == {paths['run_01'], paths['run_02']}   # 最早 2 个含 done


# ── 设置项（R4 schedule_notify，review P2-7）──────────────


def test_settings_schedule_notify_default_off(tmp_db):
    """R4：订阅完成通知默认静默（schedule_notify 缺省 False，入全量保存
    白名单）；设置页开启后持久化，ActivityMonitor 即时重读。"""
    assert tmp_db.get_settings()["schedule_notify"] is False
    tmp_db.save_settings({"schedule_notify": True})
    assert tmp_db.get_settings()["schedule_notify"] is True


# ── 删除级联 ─────────────────────────────────────────────────


def test_delete_schedule_cascades_runs(tmp_db):
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00',
                               next_run_at=None)
    tmp_db.insert_schedule_run('run_x', s['id'], None)
    assert tmp_db.delete_schedule(s['id'])
    assert tmp_db.list_run_inbox() == []
    assert not tmp_db.delete_schedule(s['id'])             # 幂等：不存在返回 False


def test_delete_schedule_nulls_task_schedule_id(tmp_db, tmp_path):
    """tasks.schedule_id 外键 ON DELETE SET NULL：订阅删除后任务行保留可查。"""
    s = tmp_db.create_schedule('a', 'p', 'general', 'general', 'daily', '09:00')
    task = tmp_db.create_task('影子任务', data_dir=str(tmp_path))
    tmp_db.conn.execute("UPDATE tasks SET schedule_id = ? WHERE id = ?",
                        (s['id'], task['id']))
    tmp_db.conn.commit()
    tmp_db.delete_schedule(s['id'])
    row = tmp_db.get_task(task['id'])
    assert row is not None and row['schedule_id'] is None
