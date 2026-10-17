import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from bson.decimal128 import Decimal128

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.main import ProvisionalUpdate, TransactionClassificationUpdate, update_provisional, update_transaction_classification, with_merchant_name
from app.platforms import platform_identity
from app.transaction_service import TransactionService
from test_ingestion import Collection, Cursor, FakeDB, event


from observed_contract import observed_update

class MerchantCollection(Collection):
    def find(self, query, projection=None):
        if "nome_padrao" in query and "$nin" in query["nome_padrao"]:
            return Cursor(doc for doc in self.docs if doc.get("nome_padrao") not in query["nome_padrao"]["$nin"])
        return Cursor(doc for doc in self.docs if self._matches(doc, query))


class PlatformTests(unittest.TestCase):
    def test_descriptors_identify_intermediary_only(self):
        for descriptor, name in (("IFD*GVG ALIM", "iFood"), ("iFood (Loja X)", "iFood"),
                                 ("99Food SMACK BURGU", "99Food"), ("99Foods KFC", "99Food"),
                                 ("RAPPI *SENDAS", "Rappi"), ("SEM PARAR * DRIVETHRU", "Sem Parar")):
            with self.subTest(descriptor=descriptor):
                self.assertEqual(platform_identity(descriptor)[1], name)
        self.assertIsNone(platform_identity("Restaurante Exemplo"))

    def test_capture_keeps_store_and_platform_separate(self):
        stamp=datetime(2026, 9, 25, 21, tzinfo=timezone.utc)
        platform_id=ObjectId()
        db=FakeDB(merchants=MerchantCollection([{"_id":platform_id,"external_key":"merchant-ifood","nome_padrao":"iFood"}]))
        result=TransactionService(db).process(event("delivery-1", "android_notification", stamp, "IFD*GVG ALIM"))
        self.assertEqual(result["status"], "provisional")
        captured=db.provisional_transactions.docs[0]
        self.assertEqual(captured["platform_id"], platform_id)
        self.assertNotIn("merchant_id", captured)

        updated=update_provisional(captured["provisional_id"], ProvisionalUpdate(vendedor="GVG Alimentos", plataforma="iFood",expected_revision=captured.get('revision',1)), db)
        self.assertEqual(updated["item"]["vendedor"], "GVG Alimentos")
        self.assertEqual(updated["item"]["fornecedor"], "GVG Alimentos")
        self.assertEqual(updated["item"]["plataforma"], "iFood")
        self.assertEqual(db.provisional_transactions.docs[0]["merchant_raw"], "IFD*GVG ALIM")
        self.assertNotEqual(db.provisional_transactions.docs[0]["merchant_id"], platform_id)
        db.provisional_transactions.docs[0]["status"]="confirmed"
        corrected=update_provisional(captured["provisional_id"], ProvisionalUpdate(vendedor="GVG Restaurante", categoria_sugerida="Alimentação", subcategoria="lanches",expected_revision=updated['item']['revision']), db)
        self.assertEqual(corrected["item"]["vendedor"],"GVG Restaurante")
        self.assertEqual(corrected["item"]["plataforma"],"iFood")
        self.assertEqual(corrected["item"]["categoria_sugerida"],"Alimentação")
        self.assertEqual(corrected["item"]["subcategoria"],"lanches")

    def test_existing_descriptor_suggests_platform_without_rewriting_source(self):
        db=FakeDB()
        result=with_merchant_name({"merchant_raw":"99Food KFC DIAMOND","valor":Decimal128("-36.50")},db)
        self.assertEqual(result["plataforma_sugerida"], "99Food")
        self.assertNotIn("fornecedor", result)

    def test_existing_transaction_saves_store_and_intermediary_separately(self):
        transaction_id=ObjectId()
        original="RAPPI *SENDAS DISTRIBI"
        db=FakeDB(transactions=Collection([{"_id":transaction_id,"estabelecimento_original":original,"valor":Decimal128("-50.00")}]), merchants=MerchantCollection())
        response=update_transaction_classification(str(transaction_id),observed_update(TransactionClassificationUpdate(vendedor="Assaí Atacadista",plataforma="Rappi"), db, str(transaction_id)),db)
        self.assertTrue(response["changed"])
        self.assertEqual(response["item"]["vendedor"],"Assaí Atacadista")
        self.assertEqual(response["item"]["fornecedor"],"Assaí Atacadista")
        self.assertEqual(response["item"]["plataforma"],"Rappi")
        self.assertEqual(db.transactions.docs[0]["estabelecimento_original"],original)
        self.assertNotEqual(db.transactions.docs[0]["merchant_id"],db.transactions.docs[0]["platform_id"])

    def test_platform_is_removed_from_vendor_when_store_is_unknown(self):
        platform_id=ObjectId()
        transaction_id=ObjectId()
        provisional_id="old-platform-as-vendor"
        db=FakeDB(
            merchants=MerchantCollection([{"_id":platform_id,"external_key":"merchant-ifood","nome_padrao":"iFood"}]),
            transactions=Collection([{"_id":transaction_id,"estabelecimento_original":"IFD*GVG ALIM","merchant_id":platform_id}]),
            provisional_transactions=Collection([{"_id":ObjectId(),"provisional_id":provisional_id,"status":"confirmed","valor":-10,"fluxo":"saida","tipo":"compra","merchant_raw":"IFD*GVG ALIM","merchant_id":platform_id,"merchant_name":"iFood"}]),
        )
        provisional=update_provisional(provisional_id,ProvisionalUpdate(plataforma="iFood"),db)["item"]
        self.assertEqual(provisional["plataforma"],"iFood")
        self.assertNotIn("vendedor",provisional)
        self.assertNotIn("merchant_id",db.provisional_transactions.docs[0])
        transaction=update_transaction_classification(str(transaction_id),observed_update(TransactionClassificationUpdate(plataforma="iFood"), db, str(transaction_id)),db)["item"]
        self.assertEqual(transaction["plataforma"],"iFood")
        self.assertNotIn("vendedor",transaction)
        self.assertNotIn("merchant_id",db.transactions.docs[0])

    def test_historical_capture_prefills_vendor_platform_category_and_details(self):
        from app.platforms import build_vendor_defaults
        stamp=datetime(2026,10,16,16,1,32,tzinfo=timezone.utc)
        vendor_id=ObjectId();platform_id=ObjectId()
        db=FakeDB(
            merchants=MerchantCollection([
                {"_id":vendor_id,"nome_padrao":"Lanche Bem","aliases":[{"valor":"LANCHE BEM","normalizado":"LANCHE BEM","status":"confirmado","source_record_ids":[]}]},
                {"_id":platform_id,"external_key":"merchant-cappta","nome_padrao":"Cappta","aliases":[]},
            ]),
            provisional_transactions=Collection([
                {"_id":ObjectId(),"provisional_id":"hist-1","status":"confirmed","revision":2,
                 "merchant_raw":"CAPPTA LAN","merchant_id":vendor_id,"merchant_name":"Lanche Bem",
                 "platform_id":platform_id,"valor":Decimal128("-6.40"),"fluxo":"saida","tipo":"compra",
                 "natureza":"COMPRA","categoria_sugerida":"Alimentação","subcategoria":"Lanches",
                 "metodo_pagamento":"cartao_debito","localidade":"Betim","data_evento":stamp},
            ]),
        )
        # 1. Existing unconfirmed capture receives historical_defaults on read
        pending={"_id":ObjectId(),"provisional_id":"prov-existing","status":"provisional","revision":1,
                 "merchant_raw":"CAPPTA LAN","valor":Decimal128("-6.40"),"fluxo":"saida","tipo":"compra","data_evento":stamp}
        enriched=with_merchant_name(pending,db)
        self.assertEqual(enriched["historical_defaults"]["vendedor"],"Lanche Bem")
        self.assertEqual(enriched["historical_defaults"]["plataforma"],"Cappta")
        self.assertEqual(enriched["historical_defaults"]["categoria_sugerida"],"Alimentação")
        self.assertEqual(enriched["historical_defaults"]["subcategoria"],"Lanches")
        self.assertEqual(enriched["historical_defaults"]["natureza"],"COMPRA")
        self.assertEqual(enriched["historical_defaults"]["metodo_pagamento"],"cartao_debito")
        self.assertEqual(enriched["historical_defaults"]["localidade"],"Betim")
        # 2. Newly ingested capture is pre-filled directly in provisional_transactions
        res=TransactionService(db).process(event("cappta-new","android_notification",stamp,"CAPPTA LAN"))
        self.assertEqual(res["status"],"provisional")
        created=next(d for d in db.provisional_transactions.docs if d["provisional_id"]==res["provisional_id"])
        self.assertEqual(created["merchant_id"],vendor_id)
        self.assertEqual(created["merchant_name"],"Lanche Bem")
        self.assertEqual(created["platform_id"],platform_id)
        self.assertEqual(created["categoria_sugerida"],"Alimentação")
        self.assertEqual(created["subcategoria"],"Lanches")
        self.assertEqual(created["natureza"],"COMPRA")
        self.assertEqual(created["metodo_pagamento"],"cartao_debito")
        self.assertEqual(created["localidade"],"Betim")
        # 3. Classification options expose vendor_defaults for interactive chip selection
        vdefs=build_vendor_defaults(db)
        self.assertEqual(vdefs["Lanche Bem"]["plataforma"],"Cappta")
        self.assertEqual(vdefs["Lanche Bem"]["categoria"],"Alimentação")
        self.assertEqual(vdefs["Lanche Bem"]["subcategoria"],"Lanches")


if __name__ == "__main__": unittest.main()
