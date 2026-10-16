import expo.modules.notificationcapture.NotificationCaptureService
import expo.modules.notificationcapture.NotificationInbox
import android.app.Notification
import android.os.Bundle
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject

fun notification(text:String,title:String="Compra aprovada",key:String="synthetic-1",big:String?=null)=StatusBarNotification("com.nu.production",Notification(Bundle(mutableMapOf(Notification.EXTRA_TITLE to title,Notification.EXTRA_TEXT to text).apply{if(big!=null)put(Notification.EXTRA_BIG_TEXT,big)})),key,1791559800000L)
fun events(service:NotificationCaptureService)=NotificationInbox.snapshot(service.prefs).getJSONArray("events")
fun main(){
  var checks=0
  fun expect(value:Boolean,message:String){check(value){message};checks++}
  for(text in listOf("Compra aprovada de R$ 32,90 em LOJA TESTE","Compra aprovada de R$ 32,90 em LOJA TESTE. Limite disponível: R$ 1.500,00","Compra aprovada de R$ 32,90 em RESTAURANTE CANCELADO","Limite disponível: R$ 1.500,00. Compra aprovada de R$ 32,90 em LOJA TESTE")){
    val service=NotificationCaptureService();service.onNotificationPosted(notification(text));expect(events(service).length()==1,"Approved purchase must be captured")
    expect(events(service).getJSONObject(0).getJSONObject("parsed_payload").getDouble("amount")==32.90,"Footer must not become purchase amount")
  }
  for(status in listOf("recusada","negada","cancelada","estornada","não aprovada")){
    val service=NotificationCaptureService();service.onNotificationPosted(notification("Compra $status de R$ 32,90 em LOJA","Compra $status"));expect(events(service).length()==0,"Refused purchase must not create capture")
  }
  val big=NotificationCaptureService();big.onNotificationPosted(notification("Resumo",big="Compra aprovada de R$ 32,90 em LOJA. Limite disponível: R$ 100,00"));expect(events(big).length()==1,"BIG_TEXT captured")
  val ambiguous=NotificationCaptureService();ambiguous.onNotificationPosted(notification("Você recebeu uma informação do cartão",title="Cartão"));expect(events(ambiguous).length()==1,"Ambiguous evidence preserved");expect(!events(ambiguous).getJSONObject(0).getJSONObject("parsed_payload").has("amount"),"Ambiguity cannot invent amount")
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
  val failed=NotificationCaptureService();NotificationInbox.snapshot(failed.prefs);failed.prefs.commitOk=false;failed.onNotificationPosted(same)
  expect(NotificationInbox.snapshot(failed.prefs).getString("state")=="unavailable","Commit failure not empty success");expect(events(failed).length()==0,"Failed capture never reported stored")
  failed.prefs.commitOk=true;failed.onNotificationPosted(same);expect(events(failed).length()==1,"Retry after commit failure")
  failed.prefs.readOk=false;expect(NotificationInbox.snapshot(failed.prefs).getString("state")=="unavailable","Read failure explicit")
  for(recoveryRaw in listOf("[", "[7,{\"source_key\":\"events\",\"raw\":\"preserved source\"}]")){
    val diagnostic=NotificationCaptureService();diagnostic.onNotificationPosted(same);diagnostic.prefs.values[NotificationInbox.KEY_RECOVERY]=recoveryRaw
    diagnostic.onNotificationPosted(notification("Compra aprovada de R$ 12,00 em NOVA",key="after-corrupt-diagnostic"))
    expect(events(diagnostic).length()==2,"Corrupt diagnostic cannot block new capture")
    val archive=JSONArray(diagnostic.prefs.values[NotificationInbox.KEY_RECOVERY]);var found=false
    for(i in 0 until archive.length())if(archive.getJSONObject(i).optString("source_key")==NotificationInbox.KEY_RECOVERY&&archive.getJSONObject(i).optString("raw")==recoveryRaw)found=true
    expect(found,"Exact corrupt recovery bytes archived atomically")
  }
  println("PASS $checks compiled Kotlin notification/inbox assertions; native Android resources are simulated")
}
