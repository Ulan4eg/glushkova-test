#!/usr/bin/env python3
"""Отчёт по договорам подрядчиков: по каждой работе - цена, выплачено, осталось
оплатить; по каждому договору - итоги. Считает на заданную дату.

Запуск:  python report.py --data data --date 2026-11-10 [--csv-dir out]
Только стандартная библиотека, Python 3.9+. Деньги - Decimal, без float.
Допущения и их обоснование - в DECISIONS.md.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class DataError(Exception):
    """Данные в файлах некорректны - считать нельзя."""


# ---------------------------------------------------------------- модели ----

@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str  # planned | done | cancelled
    completed_at: Optional[date]
    cancelled_at: Optional[date]


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: Optional[str]
    purpose: str
    amount: Decimal
    status: str  # paid | queued | refunded
    created_at: date
    paid_at: Optional[date]
    refunded_at: Optional[date]


@dataclass
class WorkLine:
    work_id: str
    contract_id: str
    title: str
    state: str  # planned | done | cancelled - на дату отчёта
    price: Decimal
    paid: Decimal
    outstanding: Decimal
    note: str = ""


@dataclass
class ContractLine:
    contract_id: str
    contractor: str
    amount: Decimal            # сумма договора по файлу
    payable: Decimal           # цены неотменённых работ
    cancelled: Decimal         # цены отменённых работ
    sent: Decimal              # всего ушло подрядчику (включая позже возвращённое)
    refunded: Decimal          # возвращено подрядчиком к дате
    net_paid: Decimal          # sent - refunded
    outstanding: Decimal       # осталось оплатить по работам
    overpaid: Decimal          # выплачено сверх цен работ
    queued: Decimal            # в очереди, не выплачено
    after_queue: Decimal       # осталось после проведения очереди (может быть < 0)
    works: List[WorkLine] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------- загрузка ---

def parse_money(raw: str, where: str) -> Decimal:
    try:
        d = Decimal((raw or "").strip())
    except InvalidOperation:
        raise DataError(f"{where}: не сумма: {raw!r}")
    if not d.is_finite() or d < 0:
        raise DataError(f"{where}: сумма должна быть неотрицательным числом: {raw!r}")
    if d != d.quantize(CENT):
        raise DataError(f"{where}: больше двух знаков после запятой: {raw!r}")
    return d.quantize(CENT)


def parse_date(raw: str, where: str, required: bool = False) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw:
        if required:
            raise DataError(f"{where}: пустая дата")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise DataError(f"{where}: дата не в формате ГГГГ-ММ-ДД: {raw!r}")


def parse_cli_date(raw: str) -> date:
    try:
        if "." in raw:
            d, m, y = (int(x) for x in raw.split("."))
            return date(y, m, d)
        return date.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"дата должна быть ГГГГ-ММ-ДД или ДД.ММ.ГГГГ: {raw!r}")


def read_rows(path: Path, columns: List[str]) -> List[Dict[str, str]]:
    if not path.exists():
        raise DataError(f"нет файла {path}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise DataError(f"{path.name}: нет колонок {', '.join(missing)}")
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def _unique(ids: List[str], what: str) -> None:
    seen = set()
    for i in ids:
        if i in seen:
            raise DataError(f"повторяется {what} {i}")
        seen.add(i)


def load(data_dir: Path):
    contracts = []
    for r in read_rows(data_dir / "contracts.csv",
                       ["contract_id", "contractor", "amount", "signed_at"]):
        w = f"contracts.csv {r['contract_id']}"
        contracts.append(Contract(r["contract_id"], r["contractor"],
                                  parse_money(r["amount"], w),
                                  parse_date(r["signed_at"], w, required=True)))
    works = []
    for r in read_rows(data_dir / "works.csv",
                       ["work_id", "contract_id", "title", "price", "status",
                        "completed_at", "cancelled_at"]):
        w = f"works.csv {r['work_id']}"
        if r["status"] not in ("planned", "done", "cancelled"):
            raise DataError(f"{w}: неизвестный статус {r['status']!r}")
        works.append(Work(r["work_id"], r["contract_id"], r["title"],
                          parse_money(r["price"], w), r["status"],
                          parse_date(r["completed_at"], w),
                          parse_date(r["cancelled_at"], w)))
    payments = []
    for r in read_rows(data_dir / "payments.csv",
                       ["payment_id", "contract_id", "work_id", "purpose", "amount",
                        "status", "created_at", "paid_at", "refunded_at"]):
        w = f"payments.csv {r['payment_id']}"
        if r["status"] not in ("paid", "queued", "refunded"):
            raise DataError(f"{w}: неизвестный статус {r['status']!r}")
        payments.append(Payment(r["payment_id"], r["contract_id"], r["work_id"] or None,
                                r["purpose"], parse_money(r["amount"], w), r["status"],
                                parse_date(r["created_at"], w, required=True),
                                parse_date(r["paid_at"], w),
                                parse_date(r["refunded_at"], w)))
    _unique([c.contract_id for c in contracts], "договор")
    _unique([w.work_id for w in works], "работа")
    _unique([p.payment_id for p in payments], "платёж")
    return contracts, works, payments


# ------------------------------------------------- состояние на дату --------

def work_state(w: Work, as_of: date, warn: List[str]) -> str:
    """Статус работы на дату отчёта (статус в выгрузке - на момент выгрузки)."""
    if w.status == "done":
        if w.completed_at is None:
            warn.append(f"{w.work_id}: статус done без даты приёмки - считаю принятой")
            return "done"
        return "done" if w.completed_at <= as_of else "planned"
    if w.status == "cancelled":
        if w.cancelled_at is None:
            warn.append(f"{w.work_id}: статус cancelled без даты отмены - считаю отменённой")
            return "cancelled"
        return "cancelled" if w.cancelled_at <= as_of else "planned"
    return "planned"


def payment_state(p: Payment, as_of: date, warn: List[str]) -> str:
    """absent | queued | paid | refunded - на дату отчёта."""
    if p.created_at > as_of:
        return "absent"
    if p.status == "queued":
        return "queued"
    paid_on = p.paid_at
    if paid_on is None:
        warn.append(f"{p.payment_id}: статус {p.status} без даты оплаты - беру дату создания")
        paid_on = p.created_at
    if paid_on > as_of:
        return "queued"  # на дату ещё не ушёл
    if p.status == "refunded":
        if p.refunded_at is None:
            warn.append(f"{p.payment_id}: refunded без даты возврата - считаю возвращённым")
            return "refunded"
        if p.refunded_at <= as_of:
            return "refunded"
    return "paid"


# ------------------------------------------------------------- расчёт -------

def build_report(contracts, works, payments, as_of: date):
    """Возвращает (список ContractLine, глобальные предупреждения)."""
    cids = {c.contract_id for c in contracts}
    for x in works:
        if x.contract_id not in cids:
            raise DataError(f"работа {x.work_id}: нет договора {x.contract_id}")
    for p in payments:
        if p.contract_id not in cids:
            raise DataError(f"платёж {p.payment_id}: нет договора {p.contract_id}")

    work_by_id = {x.work_id: x for x in works}
    global_warn: List[str] = []
    result: List[ContractLine] = []

    for c in sorted(contracts, key=lambda c: c.contract_id):
        if c.signed_at > as_of:
            global_warn.append(f"{c.contract_id}: подписан {c.signed_at}, после даты отчёта - пропущен")
            continue
        warn: List[str] = []
        cworks = [w for w in works if w.contract_id == c.contract_id]
        states = {w.work_id: work_state(w, as_of, warn) for w in cworks}

        # порядок погашения: принятые по дате приёмки, затем ещё не принятые по номеру
        done = sorted((w for w in cworks if states[w.work_id] == "done"),
                      key=lambda w: (w.completed_at or date.min, w.work_id))
        planned = sorted((w for w in cworks if states[w.work_id] == "planned"),
                         key=lambda w: w.work_id)
        order = done + planned

        linked = {w.work_id: ZERO for w in cworks}
        pool = ZERO
        sent = refunded = queued = ZERO

        for p in sorted((p for p in payments if p.contract_id == c.contract_id),
                        key=lambda p: (p.created_at, p.payment_id)):
            st = payment_state(p, as_of, warn)
            if st == "absent":
                continue
            if st == "queued":
                queued += p.amount
                continue
            sent += p.amount
            if st == "refunded":
                refunded += p.amount
                continue
            if p.work_id is None:
                pool += p.amount
                continue
            w = work_by_id.get(p.work_id)
            if w is None or w.contract_id != c.contract_id:
                warn.append(f"{p.payment_id}: работа {p.work_id} не относится к {c.contract_id} - "
                            f"зачтён как платёж без привязки")
                pool += p.amount
            elif states[w.work_id] == "cancelled":
                warn.append(f"{p.payment_id}: привязан к отменённой {w.work_id} - "
                            f"зачтён в общий аванс договора, проверьте возврат/зачёт")
                pool += p.amount
            else:
                take = min(p.amount, w.price - linked[w.work_id])
                linked[w.work_id] += take
                if take < p.amount:
                    warn.append(f"{p.payment_id}: сверх цены {w.work_id} на {p.amount - take} - "
                                f"остаток зачтён в общий аванс договора")
                    pool += p.amount - take

        alloc = {w.work_id: ZERO for w in cworks}
        for w in order:
            take = min(w.price - linked[w.work_id], pool)
            alloc[w.work_id] = take
            pool -= take
        overpaid = pool

        lines = []
        for w in sorted(cworks, key=lambda w: w.work_id):
            st = states[w.work_id]
            paid = linked[w.work_id] + alloc[w.work_id]
            notes = []
            if st == "cancelled":
                outstanding = ZERO
                notes.append("отменена, не к оплате")
            else:
                outstanding = w.price - paid
                if linked[w.work_id]:
                    notes.append(f"в т.ч. привязанный платёж {fmt(linked[w.work_id])}")
                if st == "planned" and paid:
                    notes.append("аванс, работа ещё не принята")
                if st == "planned" and w.status == "done" and w.completed_at and w.completed_at > as_of:
                    notes.append(f"принята {w.completed_at:%d.%m.%Y}, после даты отчёта")
            lines.append(WorkLine(w.work_id, c.contract_id, w.title, st, w.price,
                                  paid, outstanding, "; ".join(notes)))

        payable = sum((w.price for w in cworks if states[w.work_id] != "cancelled"), ZERO)
        cancelled = sum((w.price for w in cworks if states[w.work_id] == "cancelled"), ZERO)
        outstanding_total = sum((l.outstanding for l in lines), ZERO)
        net = sent - refunded

        # контрольное равенство: все выплаченные деньги учтены либо в работах, либо в переплате
        assert net == sum((l.paid for l in lines if l.state != "cancelled"), ZERO) + overpaid, \
            f"{c.contract_id}: не сходится баланс"

        if payable + cancelled != c.amount:
            warn.append(f"сумма цен всех работ {fmt(payable + cancelled)} не равна сумме договора "
                        f"{fmt(c.amount)}")
        if cancelled:
            warn.append(f"отменены работы на {fmt(cancelled)}; сумма договора в файле не менялась")
        if overpaid:
            warn.append(f"выплачено сверх цен работ: {fmt(overpaid)}")
        after_queue = outstanding_total - queued
        if after_queue < 0:
            warn.append(f"очередь платежей {fmt(queued)} больше остатка {fmt(outstanding_total)} - "
                        f"получится переплата {fmt(-after_queue)}")

        result.append(ContractLine(c.contract_id, c.contractor, c.amount, payable, cancelled,
                                   sent, refunded, net, outstanding_total, overpaid, queued,
                                   after_queue, lines, warn))
    return result, global_warn


# ------------------------------------------------------------- вывод --------

STATE_RU = {"done": "принята", "planned": "в плане", "cancelled": "отменена"}


def fmt(d: Decimal) -> str:
    return f"{d:,.2f}".replace(",", " ").replace(".", ",")


def render(report: List[ContractLine], global_warn: List[str], as_of: date) -> str:
    out = [f"ОТЧЁТ ПО ДОГОВОРАМ НА {as_of:%d.%m.%Y}", ""]
    for c in report:
        out.append(f"Договор {c.contract_id} - {c.contractor}")
        head = ["Работа", "Название", "Статус", "Цена", "Выплачено", "Осталось", "Примечание"]
        rows = [[l.work_id, l.title, STATE_RU[l.state], fmt(l.price), fmt(l.paid),
                 fmt(l.outstanding), l.note] for l in c.works]
        widths = [max(len(r[i]) for r in [head] + rows) for i in range(6)]
        num = {3, 4, 5}

        def line(r):
            cells = [r[i].rjust(widths[i]) if i in num else r[i].ljust(widths[i]) for i in range(6)]
            return "  " + "  ".join(cells) + ("  " + r[6] if r[6] else "")
        out.append(line(head))
        out.extend(line(r) for r in rows)
        out.append("  Итого по договору:")
        for label, val in [
            ("Сумма договора (по файлу)", c.amount),
            ("  к оплате по работам (без отменённых)", c.payable),
            ("  отменённые работы", c.cancelled),
            ("Ушло подрядчику", c.sent),
            ("  возвращено подрядчиком", c.refunded),
            ("Выплачено (чистыми)", c.net_paid),
            ("ОСТАЛОСЬ ОПЛАТИТЬ", c.outstanding),
            ("Переплата", c.overpaid),
            ("В очереди на оплату (не выплачено)", c.queued),
            ("Останется после очереди", c.after_queue),
        ]:
            out.append(f"    {label.ljust(42)} {fmt(val).rjust(14)}")
        for w in c.warnings:
            out.append(f"  ! {w}")
        out.append("")
    for w in global_warn:
        out.append(f"! {w}")
    return "\n".join(out).rstrip() + "\n"


def write_csv(report: List[ContractLine], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "works_report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["contract_id", "work_id", "title", "state", "price", "paid", "outstanding", "note"])
        for c in report:
            for l in c.works:
                w.writerow([c.contract_id, l.work_id, l.title, l.state,
                            l.price, l.paid, l.outstanding, l.note])
    with (out_dir / "contracts_report.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["contract_id", "contractor", "contract_amount", "payable", "cancelled",
                    "sent", "refunded", "net_paid", "outstanding", "overpaid", "queued",
                    "after_queue", "warnings"])
        for c in report:
            w.writerow([c.contract_id, c.contractor, c.amount, c.payable, c.cancelled, c.sent,
                        c.refunded, c.net_paid, c.outstanding, c.overpaid, c.queued,
                        c.after_queue, " | ".join(c.warnings)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Отчёт по оплате договоров на дату")
    ap.add_argument("--data", type=Path, default=Path("data"), help="папка с CSV (по умолчанию data)")
    ap.add_argument("--date", type=parse_cli_date, required=True, help="ГГГГ-ММ-ДД или ДД.ММ.ГГГГ")
    ap.add_argument("--csv-dir", type=Path, help="дополнительно сохранить отчёт в CSV в эту папку")
    args = ap.parse_args(argv)
    try:
        contracts, works, payments = load(args.data)
        report, gw = build_report(contracts, works, payments, args.date)
    except DataError as e:
        print(f"Ошибка в данных: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(report, gw, args.date))
    if args.csv_dir:
        write_csv(report, args.csv_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
