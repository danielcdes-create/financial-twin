import expo.modules.notificationcapture.NotificationCaptureModule
import expo.modules.notificationcapture.NotificationCaptureService
import expo.modules.notificationcapture.NotificationInbox
import android.app.Notification
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SpannableStub
import android.provider.Settings
import android.service.notification.FakePrefs
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URI
import java.nio.file.Files
import kotlin.system.exitProcess

fun notification(
  text:CharSequence,
  title:CharSequence="Compra aprovada",
  key:String="synthetic-1",
  big:CharSequence?=null,
  pkg:String="com.nu.production",
  postTime:Long=1791559800000L
)=StatusBarNotification(
  pkg,
  Notification(
    Bundle(
      mutableMapOf<String,CharSequence>(
        Notification.EXTRA_TITLE to title,
        Notification.EXTRA_TEXT to text
      ).apply{if(big!=null)put(Notification.EXTRA_BIG_TEXT,big)}
    )
  ),
  key,
  postTime
)

fun events(service:NotificationCaptureService)=NotificationInbox.snapshot(service.prefs).getJSONArray("events")

fun resetInboxFailureFlag(){
  val scratch=NotificationCaptureService()
  scratch.onNotificationPosted(notification("Compra aprovada de R$ 1,00 em RESET",key="reset-flag"))
}

fun main(){
  var passed=0
  val failures=mutableListOf<String>()
  fun expect(value:Boolean,message:String){
    if(value){
      passed++
    }else{
      failures.add(message)
      System.err.println("FAIL: $message")
    }
  }

  expect(Notification.EXTRA_TITLE=="android.title","Notification.EXTRA_TITLE matches Android SDK constant")
  expect(Notification.EXTRA_TEXT=="android.text","Notification.EXTRA_TEXT matches Android SDK constant")
  expect(Notification.EXTRA_BIG_TEXT=="android.bigText","Notification.EXTRA_BIG_TEXT matches Android SDK constant")

  for(text in listOf("Compra aprovada de R$ 32,90 em LOJA TESTE","Compra aprovada de R$ 32,90 em LOJA TESTE. Limite disponível: R$ 1.500,00","Compra aprovada de R$ 32,90 em RESTAURANTE CANCELADO","Limite disponível: R$ 1.500,00. Compra aprovada de R$ 32,90 em LOJA TESTE")){
    val service=NotificationCaptureService();service.onNotificationPosted(notification(text));expect(events(service).length()==1,"Approved purchase must be captured")
    expect(events(service).getJSONObject(0).getJSONObject("parsed_payload").getDouble("amount")==32.90,"Footer must not become purchase amount")
  }
  for(status in listOf("recusada","negada","cancelada","estornada","não aprovada")){
    val service=NotificationCaptureService();service.onNotificationPosted(notification("Compra $status de R$ 32,90 em LOJA","Compra $status"));expect(events(service).length()==0,"Refused purchase must not create capture")
  }
  val pixOut=NotificationCaptureService();pixOut.onNotificationPosted(notification("Você enviou um Pix de R$ 85,50 para PADARIA CENTRAL",title="Pix enviado",key="pix-out"))
  expect(events(pixOut).length()==1,"Outgoing Pix must be captured")
  expect(events(pixOut).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_method")=="pix","Outgoing Pix sets payment_method=pix")
  expect(events(pixOut).getJSONObject(0).getJSONObject("parsed_payload").optString("direction")=="expense","Outgoing Pix sets expense")
  val pixIn=NotificationCaptureService();pixIn.onNotificationPosted(notification("Você recebeu um Pix de R$ 250,00 de CLIENTE EXEMPLO",title="Pix recebido",key="pix-in"))
  expect(events(pixIn).length()==1,"Incoming Pix must be captured")
  expect(events(pixIn).getJSONObject(0).getJSONObject("parsed_payload").optString("direction")=="income","Incoming Pix sets income")
  val boletoPaid=NotificationCaptureService();boletoPaid.onNotificationPosted(notification("Pagamento de boleto realizado de R$ 199,90 para ENERGIA SA",title="Pagamento realizado",key="boleto-1"))
  expect(events(boletoPaid).length()==1,"Boleto payment must be captured")
  expect(events(boletoPaid).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_method")=="boleto","Boleto sets payment_method=boleto")
  val pixRefused=NotificationCaptureService();pixRefused.onNotificationPosted(notification("Pix recusado de R$ 50,00 para LOJA",title="Pix recusado",key="pix-refused"))
  expect(events(pixRefused).length()==0,"Refused Pix must not create capture")
  val big=NotificationCaptureService();big.onNotificationPosted(notification("Resumo",big="Compra aprovada de R$ 32,90 em LOJA. Limite disponível: R$ 100,00"));expect(events(big).length()==1,"BIG_TEXT captured")
  val ambiguous=NotificationCaptureService();ambiguous.onNotificationPosted(notification("Você recebeu uma informação do cartão",title="Cartão"));expect(events(ambiguous).length()==1,"Ambiguous evidence preserved");expect(!events(ambiguous).getJSONObject(0).getJSONObject("parsed_payload").has("amount"),"Ambiguity cannot invent amount")

  // Itaú formats, debit purchase, bank transfer, card last4, non-String CharSequence (SpannableString), and package allowlist
  val itauDated=NotificationCaptureService()
  itauDated.onNotificationPosted(notification("Compra aprovada de R$ 45,90 em PADARIA ESTRELA no dia 10/11 às 14:35. Cartão final 4321.",title="Itaú Unibanco",key="itau-dated",pkg="com.itau"))
  expect(events(itauDated).length()==1,"Itaú dated purchase notification captured")
  val itauDatedPayload=events(itauDated).getJSONObject(0).getJSONObject("parsed_payload")
  expect(itauDatedPayload.getDouble("amount")==45.90,"Itaú dated purchase amount parsed")
  expect(itauDatedPayload.optString("merchant_raw")=="PADARIA ESTRELA","Itaú dated purchase merchant extracted")
  expect(itauDatedPayload.optString("payment_instrument_hint")=="4321","Card last4 extracted from final 4321")
  expect(events(itauDated).getJSONObject(0).getString("captured_at").contains("-11-10T17:35:00.000Z"),"Itaú purchase timestamp converted from America/Sao_Paulo")

  val itauCard=NotificationCaptureService()
  itauCard.onNotificationPosted(notification("Compra aprovada no COMPRA CARTAO final 4321 valor R$ 45,90 em 10/11",title="Compra aprovada",key="itau-card",pkg="com.itau"))
  expect(events(itauCard).length()==1,"Itaú COMPRA CARTAO notification captured")
  expect(events(itauCard).getJSONObject(0).getJSONObject("parsed_payload").getDouble("amount")==45.90,"Itaú COMPRA CARTAO amount parsed")
  expect(events(itauCard).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_instrument_hint")=="4321","Itaú COMPRA CARTAO last4 parsed")

  val debit=NotificationCaptureService()
  debit.onNotificationPosted(notification("Compra no débito aprovada de R$ 28,50 em MERCADO BAIRRO cartão final 9876",title="Compra no débito aprovada",key="debit-1",pkg="com.santander.app"))
  expect(events(debit).length()==1,"Allowlisted Santander debit purchase captured")
  expect(events(debit).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_method")=="cartao_debito","Debit purchase sets payment_method=cartao_debito")
  expect(events(debit).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_instrument_hint")=="9876","Debit purchase card last4 extracted")

  val transferOut=NotificationCaptureService()
  transferOut.onNotificationPosted(notification("Sua transferência foi enviada de R$ 310,00 para JOAO SILVA",title="Transferência enviada",key="ted-out",pkg="br.com.intermedium"))
  expect(events(transferOut).length()==1,"Bank transfer notification captured")
  expect(events(transferOut).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_method")=="transferencia","Transfer sets payment_method=transferencia")
  expect(events(transferOut).getJSONObject(0).getJSONObject("parsed_payload").optString("direction")=="expense","Outgoing transfer sets direction=expense")

  val unknownPkg=NotificationCaptureService()
  unknownPkg.onNotificationPosted(notification("Compra aprovada de R$ 45,90 em LOJA TESTE",title="Compra aprovada",key="wa-1",pkg="com.whatsapp"))
  expect(events(unknownPkg).length()==0,"Non-allowlisted package com.whatsapp must be rejected")

  val spannableSvc=NotificationCaptureService()
  val spannableTitle:CharSequence=SpannableStub("Compra aprovada")
  val spannableBody:CharSequence=SpannableStub("Compra aprovada de R$ 64,20 em LIVRARIA CENTRAL no cartão final 1234")
  expect(spannableBody !is String,"SpannableStub exercises non-String CharSequence path")
  spannableSvc.onNotificationPosted(notification(spannableBody,title=spannableTitle,key="spannable-1",pkg="com.bradesco"))
  expect(events(spannableSvc).length()==1,"Spannable CharSequence notification captured")
  expect(events(spannableSvc).getJSONObject(0).getJSONObject("parsed_payload").getDouble("amount")==64.20,"Spannable CharSequence amount parsed")
  expect(events(spannableSvc).getJSONObject(0).getJSONObject("parsed_payload").optString("payment_instrument_hint")=="1234","Spannable CharSequence last4 parsed")

  val same=notification("Compra aprovada de R$ 32,90 em LOJA")
  val replay=NotificationCaptureService();replay.onNotificationPosted(same);replay.onNotificationPosted(same);expect(events(replay).length()==1,"Replay idempotent")
  replay.onNotificationPosted(notification("Compra aprovada de R$ 32,90 em LOJA",key="distinct"));expect(events(replay).length()==2,"Independent occurrences preserved")
  for(raw in listOf("{\"original\":true}","[", "null")){
    val corrupt=NotificationCaptureService();corrupt.prefs.values[NotificationCaptureService.KEY_EVENTS]=raw;corrupt.onNotificationPosted(same)
    expect(events(corrupt).length()==1,"Damaged legacy does not block new capture")
    expect(corrupt.prefs.values[NotificationCaptureService.KEY_EVENTS]==raw,"Original raw bytes preserved")
    expect(JSONObject(NotificationInbox.export(corrupt.prefs)).getString("legacy_source")==raw,"Original exportable")
    expect(NotificationInbox.snapshot(corrupt.prefs).getString("state")=="needs_recovery","Explicit recovery state")
    NotificationInbox.markSynced(corrupt.prefs,events(corrupt).getJSONObject(0).getString("event_id"));expect(events(corrupt).length()==0,"ACK retires only valid event")
  }
  val seed=events(replay).getJSONObject(0)
  val mixed=NotificationCaptureService();val raw=JSONArray().put(seed).put(JSONObject().put("event_id","broken")).put(7).toString();mixed.prefs.values[NotificationCaptureService.KEY_EVENTS]=raw
  mixed.onNotificationPosted(notification("Compra aprovada de R$ 11,00 em NOVA",key="new"));expect(events(mixed).length()==2,"Valid entries survive invalid neighbor")
  expect(events(mixed).getJSONObject(0).getString("event_id")==seed.getString("event_id"),"Original identity survives recovery")
  val archive=mixed.prefs.values[NotificationInbox.KEY_RECOVERY];NotificationInbox.snapshot(mixed.prefs);expect(mixed.prefs.values[NotificationInbox.KEY_RECOVERY]==archive,"Recovery repeat preserves archive")
  mixed.prefs.values[NotificationInbox.KEY_INBOX]="{";mixed.onNotificationPosted(same);expect(events(mixed).length()==1,"Damaged v2 recovers new captures");expect(mixed.prefs.values[NotificationInbox.KEY_RECOVERY]!!.contains("active_source")==false,"Recovery archive remains original bytes")
  val oversized=NotificationCaptureService();oversized.prefs.values[NotificationInbox.KEY_INBOX]="{" + "x".repeat(40000);oversized.onNotificationPosted(same)
  val oversizedArchive=JSONArray(oversized.prefs.values[NotificationInbox.KEY_RECOVERY])
  expect(oversizedArchive.getJSONObject(0).getString("raw").length==NotificationInbox.MAX_RECOVERY_ITEM_CHARS,"Oversized recovery item is bounded")

  // MAX_RECOVERY_ENTRIES trimming (>16 entries)
  val manyRecovery=NotificationCaptureService()
  manyRecovery.onNotificationPosted(same)
  val bigRecoveryArr=JSONArray()
  for(idx in 0 until (NotificationInbox.MAX_RECOVERY_ENTRIES+5)){
    bigRecoveryArr.put(JSONObject().put("source_key","events").put("raw","corrupt-$idx").put("reason","invalid_notification_inbox").put("recorded_at_ms",1000L+idx))
  }
  manyRecovery.prefs.values[NotificationInbox.KEY_RECOVERY]=bigRecoveryArr.toString()
  val trimmedSnap=NotificationInbox.snapshot(manyRecovery.prefs)
  expect(trimmedSnap.getInt("recovery_count")==NotificationInbox.MAX_RECOVERY_ENTRIES,"Recovery archive trimmed to MAX_RECOVERY_ENTRIES (16)")
  val storedRecovery=JSONArray(manyRecovery.prefs.values[NotificationInbox.KEY_RECOVERY])
  expect(storedRecovery.length()==NotificationInbox.MAX_RECOVERY_ENTRIES,"Persisted recovery_v2 array capped at 16 entries")
  expect(storedRecovery.getJSONObject(0).getString("raw")=="corrupt-5","Oldest recovery entries trimmed first")

  val overflow=NotificationCaptureService()
  for(idx in 0 until (NotificationInbox.MAX_INBOX_EVENTS+5)){overflow.onNotificationPosted(notification("Compra aprovada de R$ 10,00 em TESTE",key="overflow-$idx"))}
  expect(NotificationInbox.snapshot(overflow.prefs).getInt("dropped_count")==5,"Dropped inbox events counted explicitly")

  val failed=NotificationCaptureService();NotificationInbox.snapshot(failed.prefs);failed.prefs.commitOk=false;failed.onNotificationPosted(same)
  expect(NotificationInbox.snapshot(failed.prefs).getString("state")=="unavailable","Commit failure not empty success");expect(events(failed).length()==0,"Failed capture never reported stored")
  failed.prefs.commitOk=true;failed.onNotificationPosted(same);expect(events(failed).length()==1,"Retry after commit failure")
  failed.prefs.readOk=false;expect(NotificationInbox.snapshot(failed.prefs).getString("state")=="unavailable","Read failure explicit")
  resetInboxFailureFlag()

  for(recoveryRaw in listOf("[", "[7,{\"source_key\":\"events\",\"raw\":\"preserved source\"}]")){
    val diagnostic=NotificationCaptureService();diagnostic.onNotificationPosted(same);diagnostic.prefs.values[NotificationInbox.KEY_RECOVERY]=recoveryRaw
    diagnostic.onNotificationPosted(notification("Compra aprovada de R$ 12,00 em NOVA",key="after-corrupt-diagnostic"))
    expect(events(diagnostic).length()==2,"Corrupt diagnostic cannot block new capture")
    val archiveArr=JSONArray(diagnostic.prefs.values[NotificationInbox.KEY_RECOVERY]);var found=false
    for(i in 0 until archiveArr.length())if(archiveArr.getJSONObject(i).optString("source_key")==NotificationInbox.KEY_RECOVERY&&archiveArr.getJSONObject(i).optString("raw")==recoveryRaw)found=true
    expect(found,"Exact corrupt recovery bytes archived atomically")
  }

  // NotificationCaptureModule bridge functions (getInboxSnapshot, getQueuedEvents, exportRecovery, markSynced, openSettings, isEnabled)
  val tempFilesDir=Files.createTempDirectory("fin-module-files-").toFile()
  try {
    val moduleSvc=NotificationCaptureService()
    moduleSvc.onNotificationPosted(notification("Compra aprovada de R$ 55,00 em FARMACIA SAUDE",key="mod-1"))
    val ctx=Context(packageName="com.financialtwin.app",filesDir=tempFilesDir,prefs=moduleSvc.prefs)
    val module=NotificationCaptureModule()
    module.appContext.reactContext=ctx
    val def=module.definition()
    expect(def.name=="NotificationCapture","Expo module name is NotificationCapture")

    expect((def.invoke("isEnabled") as Boolean)==false,"isEnabled is false when no listeners configured")
    Settings.Secure.putString(ctx.contentResolver,"enabled_notification_listeners","com.other.app/.Svc:com.financialtwin.app/expo.modules.notificationcapture.NotificationCaptureService")
    expect((def.invoke("isEnabled") as Boolean)==true,"isEnabled is true when exact package component is enabled")
    Settings.Secure.putString(ctx.contentResolver,"enabled_notification_listeners","com.financialtwin.app.fake/expo.modules.notificationcapture.NotificationCaptureService")
    expect((def.invoke("isEnabled") as Boolean)==false,"isEnabled must reject partial package-name prefix com.financialtwin.app.fake")

    def.invoke("openSettings")
    expect(ctx.startedIntents.size==1 && ctx.startedIntents[0].action==Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS,"openSettings launches ACTION_NOTIFICATION_LISTENER_SETTINGS")
    expect((ctx.startedIntents[0].flags and Intent.FLAG_ACTIVITY_NEW_TASK)!=0,"openSettings sets FLAG_ACTIVITY_NEW_TASK")

    val snapJson=JSONObject(def.invoke("getInboxSnapshot") as String)
    expect(snapJson.getInt("version")==2 && snapJson.getString("state")=="ready","getInboxSnapshot returns ready v2 snapshot")
    val queuedArr=JSONArray(def.invoke("getQueuedEvents") as String)
    expect(queuedArr.length()==1,"getQueuedEvents returns serialized event array")
    val modEventId=queuedArr.getJSONObject(0).getString("event_id")

    val exportedUri=def.invoke("exportRecovery") as String
    expect(exportedUri.startsWith("file:"),"exportRecovery returns file URI")
    val exportedFile=File(URI(exportedUri))
    expect(exportedFile.exists(),"exportRecovery writes recovery JSON file")
    val exportedJson=JSONObject(exportedFile.readText(Charsets.UTF_8))
    expect(exportedJson.getInt("version")==2 && exportedJson.optString("active_source").contains(modEventId),"exportRecovery preserves active_source")

    def.invoke("markSynced",modEventId)
    expect(JSONArray(def.invoke("getQueuedEvents") as String).length()==0,"markSynced removes acknowledged event")

    moduleSvc.prefs.readOk=false
    var threwOnUnavailable=false
    try { def.invoke("getQueuedEvents") } catch(_:IllegalStateException) { threwOnUnavailable=true }
    expect(threwOnUnavailable,"getQueuedEvents throws when inbox state is unavailable")
    moduleSvc.prefs.readOk=true
    resetInboxFailureFlag()
  } finally {
    tempFilesDir.deleteRecursively()
  }

  // BUG-10: Transient failure recovery on an already-initialized inbox_v2 without requiring a new write()
  val transientSvc=NotificationCaptureService()
  transientSvc.onNotificationPosted(notification("Compra aprovada de R$ 77,00 em SUPERMERCADO",key="transient-v2-1"))
  val initialSnap=NotificationInbox.snapshot(transientSvc.prefs)
  expect(initialSnap.getString("state")=="ready" && initialSnap.getJSONArray("events").length()==1,"Seeded inbox_v2 starts ready with 1 event")
  transientSvc.prefs.readOk=false
  expect(NotificationInbox.snapshot(transientSvc.prefs).getString("state")=="unavailable","Transient read failure marks snapshot unavailable")
  transientSvc.prefs.readOk=true
  val recoveredSnap=NotificationInbox.snapshot(transientSvc.prefs)
  expect(recoveredSnap.getString("state")=="ready","BUG-10: snapshot() must recover to 'ready' once SharedPreferences read succeeds again")
  expect(recoveredSnap.getString("error")=="","BUG-10: snapshot() must clear stale error message after transient read recovery")
  expect(recoveredSnap.getJSONArray("events").length()==1,"Preserved event remains available after transient read recovery")
  resetInboxFailureFlag()

  if(failures.isNotEmpty()){
    System.err.println("FAILED ${failures.size} of ${passed+failures.size} Kotlin notification/inbox assertions.")
    exitProcess(1)
  }
  println("PASS $passed compiled Kotlin notification/inbox assertions; native Android resources are simulated")
}
