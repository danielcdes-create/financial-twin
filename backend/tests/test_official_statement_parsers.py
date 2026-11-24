"""End-to-end synthetic multi-page PDF tests for official_statement_parsers.py."""
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app.official_statement_parsers import (
    _extract_in_memory_cpf_candidates,
    correlate_parsed_statements,
    deduplicate_overlapping_bank_statements,
    parse_official_pdf,
    parse_official_pdf_batch,
)


def _build_pdf_bytes(pages: list[list[str]], *, user_pw: str | None = None) -> bytes:
    doc = pymupdf.open()
    try:
        for page_lines in pages:
            page = doc.new_page(width=595, height=842)
            y = 36
            for line in page_lines:
                page.insert_text((36, y), line, fontsize=9)
                y += 12
        if user_pw:
            return doc.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner-master-pw",
                user_pw=user_pw,
            )
        return doc.tobytes()
    finally:
        doc.close()


def _write_pdf(directory: str, filename: str, pages: list[list[str]], *, user_pw: str | None = None) -> Path:
    path = Path(directory) / filename
    path.write_bytes(_build_pdf_bytes(pages, user_pw=user_pw))
    return path


class OfficialStatementParsersTests(unittest.TestCase):
    def test_itau_card_invoice_multipage_national_intl_iof_pgconta_and_math_validation(self):
        pages = [
            [
                "Resumo da fatura em R$",
                "Titular DANIEL TESTE",
                "Cartão 5432.XXXX.XXXX.1234",
                "Vencimento: 10/11/2026",
                "Total da fatura anterior",
                "500,00",
                "Pagamento efetuado em 10/10/2026",
                "-500,00",
                "Total dos pagamentos",
                "-500,00",
                "Lançamentos: compras e saques",
                "DATA",
                "ESTABELECIMENTO",
                "VALOR EM R$",
                "02/11",
                "IFD *ZAMP SABEL",
                "45,90",
                "ALIMENTAÇÃO .SP",
                "15/09",
                "MAGAZINE LUIZA 02/06",
                "100,00",
                "continua...",
                "1",
            ],
            [
                "2",
                "03/11",
                "ESTORNO UBER",
                "-10,00",
                "04/11",
                "CREDITO COMPRA LOJA",
                "5,00-",
                "Lançamentos no cartão",
                "Lançamentos internacionais",
                "05/11 OPENAI *CHATGPT",
                "100,00",
                "Repasse de IOF em R$",
                "3,50",
                "Lançamentos: produtos e serviços",
                "06/11",
                "PIX NO CARTAO LOJA",
                "105,50",
                "Principal (R$ 100,00) + Juros (R$ 5,50)",
                "Lançamentos produtos e serviços",
                "Total desta fatura",
                "339,90",
            ],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "fatura_itau_nov.pdf", pages)
            parsed = parse_official_pdf(pdf_path)

            self.assertEqual(parsed.kind, "card_statement")
            self.assertEqual(parsed.institution, "Itaú")
            self.assertEqual(parsed.card_final, "1234")
            self.assertEqual(parsed.competencia, "2026-11")
            self.assertEqual(parsed.due_date, "2026-11-10")
            self.assertEqual(parsed.total_amount, "339.90")
            self.assertEqual(parsed.status, "aberta")

            prior_credits = [it for it in parsed.items if it.skip_insert]
            self.assertEqual(len(prior_credits), 1)
            self.assertEqual(prior_credits[0].kind, "bill_payment_credit")
            self.assertEqual(prior_credits[0].amount, "-500.00")

            active = [it for it in parsed.items if not it.skip_insert]
            self.assertEqual(len(active), 7)

            # 1. National purchase with category hint
            self.assertEqual(active[0].popular_vendor, "Burger King")
            self.assertEqual(active[0].platform, "iFood")
            self.assertEqual(active[0].amount, "-45.90")
            self.assertEqual(active[0].bank_category_hint, "ALIMENTAÇÃO .SP")

            # 2. National installment 02/06 without category hint
            self.assertEqual(active[1].installment_number, 2)
            self.assertEqual(active[1].installment_total, 6)
            self.assertTrue(active[1].prior_cycle_installment)
            self.assertEqual(active[1].date, "2026-09-15")
            self.assertEqual(active[1].statement_month, "2026-11")

            # 3 & 4. Refunds (- prefix and - suffix)
            self.assertEqual(active[2].kind, "refund")
            self.assertEqual(active[2].amount, "10.00")
            self.assertEqual(active[2].natureza, "ESTORNO")
            self.assertEqual(active[3].kind, "refund")
            self.assertEqual(active[3].amount, "5.00")
            self.assertEqual(active[3].natureza, "ESTORNO")

            # 5 & 6. International purchase + Repasse de IOF
            self.assertEqual(active[4].popular_vendor, "ChatGPT")
            self.assertEqual(active[4].amount, "-100.00")
            self.assertEqual(active[5].description, "IOF Compra Internacional")
            self.assertEqual(active[5].amount, "-3.50")
            self.assertEqual(active[5].natureza, "IMPOSTO_TRIBUTO")

            # 7. Produtos e serviços with Principal + Juros split
            self.assertEqual(active[6].amount, "-105.50")
            self.assertEqual(active[6].principal_amount, "-100.00")
            self.assertEqual(active[6].fee_amount, "-5.50")

            # Math mismatch must raise ValueError
            bad_pages = [list(pages[0]), [*pages[1][:-1], "999,99"]]
            bad_path = _write_pdf(tmp, "fatura_itau_bad.pdf", bad_pages)
            with self.assertRaisesRegex(ValueError, "Divergência matemática"):
                parse_official_pdf(bad_path)

    def test_itau_card_multiyear_12_of_12_installment_resolves_prior_year_purchase_date(self):
        # BUG-08: 25/03 on a 2026-03 bill for installment 12/12 was bought on 2025-03-25, NOT 2026-03-25
        pages = [
            [
                "Resumo da fatura em R$",
                "Titular DANIEL TESTE",
                "Cartão 5432.XXXX.XXXX.1234",
                "Com vencimento em:",
                "15/03/2026",
                "Total dos pagamentos",
                "0,00",
                "25/03",
                "APPLE STORE 12/12",
                "250,00",
                "Lançamentos no cartão",
                "Total desta fatura",
                "250,00",
            ]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "fatura_itau_12_12.pdf", pages)
            parsed = parse_official_pdf(pdf_path)
            active = [it for it in parsed.items if not it.skip_insert]
            self.assertEqual(len(active), 1)
            item = active[0]
            self.assertEqual(item.installment_number, 12)
            self.assertEqual(item.installment_total, 12)
            self.assertEqual(item.date, "2025-03-25")
            self.assertTrue(item.prior_cycle_installment)

    def test_itau_card_carryover_saldo_anterior_and_multiple_midcycle_payments(self):
        # BUG-08: Carryover balance from partial prior payment + multiple mid-cycle payments (antecipação)
        # Prior bill = 500,00; payments = -300,00 (partial) and -50,00 (antecipação) => Total dos pagamentos = -350,00;
        # New purchase = 100,00 => Total desta fatura = 500,00 - 350,00 + 100,00 = 250,00.
        pages = [
            [
                "Resumo da fatura em R$",
                "Titular DANIEL TESTE",
                "Cartão 5432.XXXX.XXXX.1234",
                "Vencimento: 10/11/2026",
                "Total da fatura anterior",
                "500,00",
                "Pagamento efetuado em 10/10/2026",
                "-300,00",
                "Pagamento efetuado em 22/10/2026",
                "-50,00",
                "Total dos pagamentos",
                "-350,00",
                "02/11",
                "SUPERMERCADO VERDEMAR",
                "100,00",
                "Lançamentos no cartão",
                "Total desta fatura",
                "250,00",
            ]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "fatura_itau_carryover.pdf", pages)
            parsed = parse_official_pdf(pdf_path)
            self.assertEqual(parsed.total_amount, "250.00")
            payment_credits = [it for it in parsed.items if it.kind == "bill_payment_credit"]
            self.assertEqual(len(payment_credits), 2)
            active = [it for it in parsed.items if not it.skip_insert]
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].amount, "-100.00")

    def test_itau_bank_statement_newest_first_reversal_transit_own_transfer_and_errors(self):
        # Itaú prints newest-first; mix inline ("DD/MM/YYYY DESC") and 3-line ("DD/MM/YYYY", "DESC", val) layouts
        pages = [
            [
                "extrato conta / lançamentos",
                "período de visualização: 01/11/2026 até 15/11/2026",
                "DANIEL TESTE",
                "123.456.789-01",
                "Agência: 1234",
                "saldo (R$)",
                "15/11/2026 SALDO DO DIA",
                "530,60",
                "10/11/2026 PIX CARTAO MERCADO",
                "-200,00",
                "10/11/2026",
                "CREDITO LIBERAD PIX",
                "200,00",
                "09/11/2026 PIX TRANSF DANIEL RESERVA",
                "-150,00",
            ],
            [
                "07/11/2026",
                "ESTORNO PIX LOJA",
                "25,00",
                "05/11/2026 PAG FATURA CARTAO ITAU",
                "-339,90",
                "03/11/2026",
                "IOF",
                "-4,50",
                "01/11/2026 SALDO DO DIA",
                "1.000,00",
                "Aviso! Os lançamentos futuros não compõem o saldo.",
                "99/99/9999 IGNORADO APOS AVISO",
                "-999,00",
            ],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "extrato_itau_nov.pdf", pages)
            parsed = parse_official_pdf(pdf_path)

            self.assertEqual(parsed.kind, "bank_statement")
            self.assertEqual(parsed.institution, "Itaú")
            self.assertEqual(parsed.account_profile_key, "itau")
            self.assertEqual(parsed.opening_balance, "1000.00")
            self.assertEqual(parsed.closing_balance, "530.60")
            self.assertEqual(parsed.period_start, "2026-11-01")
            self.assertEqual(parsed.period_end, "2026-11-15")

            # Chronological reversal: oldest (03/11) first, newest (10/11) last
            self.assertEqual([it.date for it in parsed.items], [
                "2026-11-03",
                "2026-11-05",
                "2026-11-07",
                "2026-11-09",
                "2026-11-10",
                "2026-11-10",
            ])
            # IOF
            self.assertEqual(parsed.items[0].natureza, "IMPOSTO_TRIBUTO")
            # PAG FATURA
            self.assertEqual(parsed.items[1].kind, "bill_payment")
            self.assertEqual(parsed.items[1].tipo, "pagamento_fatura")
            # ESTORNO PIX
            self.assertEqual(parsed.items[2].kind, "refund")
            self.assertEqual(parsed.items[2].natureza, "ESTORNO")
            # PIX TRANSF <HOLDER>
            self.assertTrue(parsed.items[3].own_transfer)
            self.assertEqual(parsed.items[3].natureza, "TRANSFERENCIA")
            # CREDITO LIBERAD PIX + PIX CARTAO technical transit pair
            self.assertTrue(parsed.items[4].technical_transit)
            self.assertTrue(parsed.items[5].technical_transit)
            self.assertEqual(
                Decimal(parsed.items[4].amount) + Decimal(parsed.items[5].amount),
                Decimal("0.00"),
            )

            # Math mismatch ValueError
            bad_math = [[*pages[0][:7], "999,99", *pages[0][8:]], list(pages[1])]
            with self.assertRaisesRegex(ValueError, "Divergência matemática"):
                parse_official_pdf(_write_pdf(tmp, "extrato_itau_bad_math.pdf", bad_math))

            # Insufficient daily balances (<2 SALDO DO DIA) ValueError
            single_bal = [pages[0], [line for line in pages[1] if "SALDO DO DIA" not in line and line != "1.000,00"]]
            with self.assertRaisesRegex(ValueError, "Saldos diários insuficientes"):
                parse_official_pdf(_write_pdf(tmp, "extrato_itau_one_bal.pdf", single_bal))

    def test_santander_bank_statement_multiline_subcard_rollover_and_math_validation(self):
        pages = [
            [
                "SANTANDER",
                "EXTRATO CONSOLIDADO",
                "janeiro/2026",
                "Cliente: DANIEL TESTE",
                "CPF: 123.456.789-01",
                "Conta Corrente",
                "Movimentação",
                "SALDO EM 29/12",
                "2.000,00",
                "30/12",
                "PIX ENVIADO",
                "PADARIA CENTRAL",
                "987654",
                "-50,00",
                "1.950,00",
                "05/01",
                "DEBITO AUT FAT.CARTAO FINAL 5678",
                "-",
                "-420,00",
            ],
            [
                "08/01",
                "TED MESMA TITULARIDADE DANIEL",
                "112233",
                "-300,00",
                "10/01",
                "ESTORNO COMPRA DEBITO",
                "-",
                "30,00",
                "12/01",
                "IOF ADICIONAL",
                "-",
                "-3,20",
                "12/01",
                "JUROS LIMITE CONTA",
                "-",
                "-6,80",
                "SALDO EM 31/01",
                "1.250,00",
            ],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "extrato_santander_jan.pdf", pages)
            parsed = parse_official_pdf(pdf_path)

            self.assertEqual(parsed.kind, "bank_statement")
            self.assertEqual(parsed.institution, "Santander")
            self.assertEqual(parsed.account_profile_key, "santander")
            self.assertEqual(parsed.opening_balance, "2000.00")
            self.assertEqual(parsed.closing_balance, "1250.00")
            # December row inside janeiro/2026 statement rolls back to 2025-12-30
            self.assertEqual(parsed.period_start, "2025-12-30")
            self.assertEqual(parsed.period_end, "2026-01-31")
            self.assertEqual(len(parsed.items), 6)

            self.assertEqual(parsed.items[0].date, "2025-12-30")
            self.assertEqual(parsed.items[0].description, "PIX ENVIADO PADARIA CENTRAL")
            self.assertEqual(parsed.items[0].bank_ref, "987654")

            self.assertEqual(parsed.items[1].kind, "bill_payment")
            self.assertEqual(parsed.items[1].subcard_final, "5678")
            self.assertIsNone(parsed.items[1].bank_ref)

            self.assertTrue(parsed.items[2].own_transfer)
            self.assertEqual(parsed.items[2].natureza, "TRANSFERENCIA")

            self.assertEqual(parsed.items[3].kind, "refund")
            self.assertEqual(parsed.items[3].natureza, "ESTORNO")

            self.assertEqual(parsed.items[4].natureza, "IMPOSTO_TRIBUTO")
            self.assertEqual(parsed.items[5].natureza, "TARIFA_ENCARGO")

            bad_pages = [list(pages[0]), [*pages[1][:-1], "9.999,00"]]
            with self.assertRaisesRegex(ValueError, "Divergência matemática"):
                parse_official_pdf(_write_pdf(tmp, "extrato_santander_bad.pdf", bad_pages))

    def test_santander_card_invoice_subcards_installments_duplicate_refunds_and_math_validation(self):
        pages = [
            [
                "SANTANDER",
                "5555 XXXX XXXX 1234",
                "Vencimento",
                "15/01/2026",
                "Detalhamento da Fatura",
                "5555 XXXX XXXX 1234",
                "10/12 DEB AUTOM DE FATURA",
                "-800,00",
                "15/11 LOJA ELETRONICOS",
                "03/10",
                "150,00",
                "VALOR TOTAL",
                "TITULAR",
                "150,00",
            ],
            [
                "5555 XXXX XXXX 5678",
                "04/01 UBER *TRIP",
                "40,00",
                "05/01 ESTORNO IFOOD",
                "-15,00",
                "05/01 ESTORNO IFOOD",
                "-15,00",
                "Resumo da Fatura",
                "(=) Saldo Desta Fatura",
                "160,00",
            ],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "fatura_santander_jan.pdf", pages)
            parsed = parse_official_pdf(pdf_path)

            self.assertEqual(parsed.kind, "card_statement")
            self.assertEqual(parsed.institution, "Santander")
            self.assertEqual(parsed.card_final, "1234")
            self.assertEqual(parsed.additional_finals, ["5678"])
            self.assertEqual(parsed.competencia, "2026-01")
            self.assertEqual(parsed.due_date, "2026-01-15")
            self.assertEqual(parsed.total_amount, "160.00")

            skipped = [it for it in parsed.items if it.skip_insert]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0].kind, "bill_payment_credit")

            active = [it for it in parsed.items if not it.skip_insert]
            self.assertEqual(len(active), 4)
            self.assertEqual(active[0].installment_number, 3)
            self.assertEqual(active[0].installment_total, 10)
            self.assertTrue(active[0].prior_cycle_installment)
            self.assertEqual(active[0].date, "2025-11-15")
            self.assertEqual(active[0].subcard_final, "1234")

            self.assertEqual(active[1].subcard_final, "5678")
            self.assertEqual(active[2].kind, "refund")
            self.assertEqual(active[3].kind, "refund")
            self.assertTrue(active[2].canonical_key.endswith(":1"))
            self.assertTrue(active[3].canonical_key.endswith(":2"))
            self.assertNotEqual(active[2].canonical_key, active[3].canonical_key)

            bad_pages = [list(pages[0]), [*pages[1][:-1], "500,00"]]
            with self.assertRaisesRegex(ValueError, "Divergência matemática"):
                parse_official_pdf(_write_pdf(tmp, "fatura_santander_bad.pdf", bad_pages))

    def test_santander_card_carryover_balance_and_partial_payment(self):
        # BUG-08: Santander invoice with Saldo Anterior (400,00) - partial payment (-250,00) + new purchase (100,00) = 250,00
        pages = [
            [
                "SANTANDER",
                "5555 XXXX XXXX 1234",
                "Vencimento",
                "15/01/2026",
                "Detalhamento da Fatura",
                "5555 XXXX XXXX 1234",
                "10/12 DEB AUTOM DE FATURA",
                "-250,00",
                "04/01 DROGARIA ARAUJO",
                "100,00",
                "Resumo da Fatura",
                "Saldo Anterior",
                "400,00",
                "(-) Pagamentos / Créditos",
                "-250,00",
                "(=) Saldo Desta Fatura",
                "250,00",
            ]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_pdf(tmp, "fatura_santander_carryover.pdf", pages)
            parsed = parse_official_pdf(pdf_path)
            self.assertEqual(parsed.total_amount, "250.00")
            active = [it for it in parsed.items if not it.skip_insert]
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].amount, "-100.00")

    def test_encryption_unlock_modes_overlap_dedup_and_subcard_correlation(self):
        card_5678_pages = [
            [
                "SANTANDER",
                "5555 XXXX XXXX 1234",
                "Vencimento",
                "10/01/2026",
                "Detalhamento da Fatura",
                "5555 XXXX XXXX 5678",
                "02/01 UBER *TRIP",
                "420,00",
                "Resumo da Fatura",
                "(=) Saldo Desta Fatura",
                "420,00",
            ]
        ]
        card_9999_pages = [
            [
                "SANTANDER",
                "5555 XXXX XXXX 9999",
                "Vencimento",
                "10/01/2026",
                "Detalhamento da Fatura",
                "5555 XXXX XXXX 9999",
                "02/01 DROGARIA ARAUJO",
                "420,00",
                "Resumo da Fatura",
                "(=) Saldo Desta Fatura",
                "420,00",
            ]
        ]
        bank_partial_pages = [
            [
                "SANTANDER",
                "EXTRATO CONSOLIDADO",
                "janeiro/2026",
                "Cliente: DANIEL TESTE",
                "CPF: 123.456.789-01",
                "Conta Corrente",
                "Movimentação",
                "SALDO EM 01/01",
                "1.000,00",
                "08/01",
                "DEBITO AUT FAT.CARTAO FINAL 5678",
                "-",
                "-420,00",
                "SALDO EM 10/01",
                "580,00",
            ]
        ]
        bank_full_pages = [
            [
                "SANTANDER",
                "EXTRATO CONSOLIDADO",
                "janeiro/2026",
                "Cliente: DANIEL TESTE",
                "CPF: 123.456.789-01",
                "Conta Corrente",
                "Movimentação",
                "SALDO EM 01/01",
                "1.000,00",
                "08/01",
                "DEBITO AUT FAT.CARTAO FINAL 5678",
                "-",
                "-420,00",
                "20/01",
                "PIX ENVIADO SUPERMERCADO",
                "-",
                "-80,00",
                "SALDO EM 31/01",
                "500,00",
            ]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Locked PDF without password raises ValueError
            enc_11_path = _write_pdf(tmp, "card_5678_enc11.pdf", card_5678_pages, user_pw="12345678901")
            with self.assertRaisesRegex(ValueError, "protegido por senha"):
                parse_official_pdf(enc_11_path)

            # 2. Explicit formatted CPF unlocks 11-digit AES-256 PDF
            unlocked_explicit = parse_official_pdf_batch([enc_11_path], cpf="123.456.789-01")
            self.assertEqual(len(unlocked_explicit), 1)
            self.assertEqual(unlocked_explicit[0].total_amount, "420.00")

            # 3. 6-digit CPF prefix unlock
            enc_6_path = _write_pdf(tmp, "card_9999_enc6.pdf", card_9999_pages, user_pw="123456")
            unlocked_6 = parse_official_pdf_batch([enc_6_path], cpf="123.456.789-01")
            self.assertEqual(unlocked_6[0].card_final, "9999")

            # 4. In-memory CPF extraction from unencrypted companion PDF + overlap dedup + subcard tie-breaking correlation
            partial_path = _write_pdf(tmp, "bank_partial.pdf", bank_partial_pages)
            full_path = _write_pdf(tmp, "bank_full.pdf", bank_full_pages)
            candidates = _extract_in_memory_cpf_candidates([enc_11_path, partial_path])
            self.assertEqual(candidates, ["12345678901"])

            batch_docs = parse_official_pdf_batch([enc_11_path, enc_6_path, partial_path, full_path])
            by_name = {d.filename: d for d in batch_docs}

            # Partial statement item duplicated in full statement is marked skip_insert=True
            self.assertTrue(all(it.skip_insert for it in by_name["bank_partial.pdf"].items))
            self.assertFalse(any(it.skip_insert for it in by_name["bank_full.pdf"].items))

            # Subcard 5678 tie-breaker marks card_5678_enc11.pdf paid on 2026-01-08 and leaves 9999 open
            self.assertEqual(by_name["card_5678_enc11.pdf"].status, "paga")
            self.assertEqual(by_name["card_5678_enc11.pdf"].paid_date, "2026-01-08")
            self.assertEqual(by_name["card_9999_enc6.pdf"].status, "aberta")
            self.assertIsNone(by_name["card_9999_enc6.pdf"].paid_date)

            # 5. Unrecognized PDF raises ValueError
            unknown_path = _write_pdf(tmp, "desconhecido.pdf", [["Recibo avulso sem formato bancário oficial"]])
            with self.assertRaisesRegex(ValueError, "não reconhecido"):
                parse_official_pdf(unknown_path)


if __name__ == "__main__":
    unittest.main()
