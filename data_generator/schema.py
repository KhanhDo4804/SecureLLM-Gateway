"""Appendix C. Names with ? are nullable. All other columns are mandatory."""
from dataclasses import dataclass
from datetime import date, datetime

import pyarrow as pa


@dataclass(frozen=True)
class Table:
    layer: str
    primary: tuple
    columns: tuple


def spec(layer, pk, fields):
    return Table(layer, tuple(pk.split()), tuple(fields.split()))


S, F, E = "source", "features", "evaluation"
TABLES = {
    "areas": spec(S, "area_id", "area_id province_code area_name catalog_version"),
    "branches": spec(S, "branch_id", "branch_id area_id branch_name"),
    "staff": spec(S, "staff_id", "staff_id branch_id role_code"),
    "companies": spec(S, "company_id", "company_id area_id company_name"),
    "merchants": spec(S, "merchant_id", "merchant_id area_id mcc merchant_name"),
    "devices": spec(S, "device_id", "device_id device_kind"),
    "customers": spec(S, "customer_id", "customer_id birth_on residence_area_id occupation_code?"),
    "customer_pii": spec(S, "customer_id", "customer_id full_name national_id_test? phone_test? email_test?"),
    "rm_assignments": spec(S, "customer_id valid_from", "customer_id valid_from staff_id valid_to?"),
    "employment": spec(S, "customer_id company_id valid_from", "customer_id company_id valid_from valid_to?"),
    "accounts": spec(S, "account_id", "account_id customer_id branch_id opened_on closed_on? account_no_test"),
    "cards": spec(S, "card_id", "card_id account_id card_kind issued_on closed_on? pan_test credit_limit_vnd?"),
    "transactions": spec(S, "txn_id", "txn_id account_id posted_at kind status amount_vnd merchant_id? payer_company_id? card_id? device_id?"),
    "customer_financials": spec(S, "customer_id as_of_on", "customer_id as_of_on monthly_income_vnd? credit_score?"),
    "loans": spec(S, "loan_id", "loan_id customer_id loan_type disbursed_on principal_vnd maturity_on"),
    "installments": spec(S, "loan_id installment_no", "loan_id installment_no due_on principal_due_vnd interest_due_vnd"),
    "payment_allocations": spec(S, "loan_id installment_no payment_ref", "loan_id installment_no payment_ref paid_at principal_paid_vnd interest_paid_vnd"),
    "loan_snapshot": spec(S, "loan_id as_of_on", "loan_id as_of_on outstanding_vnd days_past_due debt_group?"),
    "object_nodes": spec(F, "object_node_id", "object_node_id node_type source_key"),
    "facts": spec(F, "fact_id", "fact_id customer_id predicate object_node_id"),
    "fact_sources": spec(E, "fact_id source_table source_key", "fact_id source_table source_key"),
    "facts_gold": spec(E, "fact_id", "fact_id is_sensitive reason_code policy_version"),
    "split_customers": spec(E, "customer_id", "customer_id split"),
    "split_families": spec(E, "family_id", "family_id split"),
    "texts": spec(F, "text_id", "text_id customer_id family_id body"),
    "fact_texts": spec(F, "fact_id text_id", "fact_id text_id"),
    "pii_spans_gold": spec(E, "text_id start_cp end_cp pii_type", "text_id start_cp end_cp pii_type"),
    "facts_input": spec(F, "fact_id", "fact_id event_kind observed_value evidence_text?"),
    "facts_test": spec(F, "fact_id", "fact_id event_kind observed_value evidence_text?"),
    "cases": spec(S, "case_id", "case_id staff_id customer_id purpose_code opened_at closed_at?"),
    "rag_chunks": spec(F, "chunk_id", "chunk_id text_id customer_id field_code content"),
    "fact_predictions": spec(E, "model_run_id fact_id", "model_run_id fact_id pred_sensitive score?"),
}

REFS = {
    "area_id": "areas", "residence_area_id": "areas", "branch_id": "branches",
    "staff_id": "staff", "company_id": "companies", "payer_company_id": "companies",
    "device_id": "devices", "merchant_id": "merchants", "customer_id": "customers",
    "account_id": "accounts", "card_id": "cards", "loan_id": "loans", "fact_id": "facts",
    "object_node_id": "object_nodes", "text_id": "texts", "family_id": "split_families",
}


def arrow_schema(table):
    fields = []
    for token in TABLES[table].columns:
        name = token.rstrip("?")
        if name.endswith("_vnd"):
            dtype = pa.int64()
        elif name in {"installment_no", "days_past_due", "debt_group", "credit_score", "start_cp", "end_cp"}:
            dtype = pa.int32()
        elif name in {"is_sensitive", "pred_sensitive"}:
            dtype = pa.bool_()
        elif name == "score":
            dtype = pa.float64()
        elif name.endswith("_on") or name in {"valid_from", "valid_to"}:
            dtype = pa.date32()
        elif name.endswith("_at"):
            dtype = pa.timestamp("us", tz="Asia/Ho_Chi_Minh")
        else:
            dtype = pa.string()
        fields.append(pa.field(name, dtype, nullable=token.endswith("?")))
    return pa.schema(fields)


def to_arrow(table, rows):
    schema = arrow_schema(table)
    columns = {}
    expected = set(schema.names)
    for row in rows:
        if set(row) != expected:
            raise ValueError(f"{table}: unexpected/missing fields {expected.symmetric_difference(row)}")
    for field in schema:
        values = [row[field.name] for row in rows]
        if not field.nullable and None in values:
            raise ValueError(f"{table}.{field.name}: unexpected NULL")
        if pa.types.is_date(field.type):
            values = [date.fromisoformat(v) if v is not None else None for v in values]
        elif pa.types.is_timestamp(field.type):
            values = [datetime.fromisoformat(v) if v is not None else None for v in values]
            if any(v is not None and v.utcoffset() is None for v in values):
                raise ValueError(f"{table}.{field.name}: timezone required")
        columns[field.name] = pa.array(values, type=field.type)
    return pa.Table.from_pydict(columns, schema=schema)


def from_arrow(data):
    rows = data.to_pylist()
    date_fields = [f.name for f in data.schema if pa.types.is_date(f.type) or pa.types.is_timestamp(f.type)]
    for row in rows:
        for name in date_fields:
            if row[name] is not None:
                row[name] = row[name].isoformat()
    return rows
