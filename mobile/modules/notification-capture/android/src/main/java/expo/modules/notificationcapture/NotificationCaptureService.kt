package expo.modules.notificationcapture

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import java.math.BigDecimal
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

class NotificationCaptureService : NotificationListenerService() {
  companion object {
    const val PREFS="financial_twin_notification_capture"
    const val KEY_EVENTS="events"
    val QUEUE_LOCK=Any()
    // Exact IDs verified on the publishers' Play listings on 2026-10-02.
    // Package origin is evidence provenance; it never bypasses human review.
    private val financialPackages=setOf("com.nu.production","com.itau","com.itau.pers",
      "com.santander.app","br.com.intermedium","com.bradesco","com.c6bank.app",
      "com.mercadopago.wallet","com.picpay","br.com.gabba.Caixa","br.com.bb.android")
    private val amountRegex=Regex("R\\$\\s*([0-9.]+,[0-9]{2})",RegexOption.IGNORE_CASE)
    private val sentenceMerchantRegex=Regex(",\\s*em\\s+([^,\\n]+),\\s*(?:aprovada|realizada|efetuada|confirmada)",RegexOption.IGNORE_CASE)
    private val itauPurchaseRegex=Regex("compra\\s+(?:no\\s+d[eé]bito\\s+)?aprovada\\s+de\\s+R\\$\\s*[0-9.]+,[0-9]{2}\\s+em\\s+(.+?)\\s+no\\s+dia\\s+(\\d{2})/(\\d{2})(?:/(\\d{2,4}))?\\s+[àa]s\\s+(\\d{2}):(\\d{2})",RegexOption.IGNORE_CASE)
    private val genericApprovalTitleRegex=Regex("^(?:olá[!,.]?\\s*)?(?:sua\\s+|seu\\s+)?(?:compra|pix|transfer[eê]ncia|pagamento|boleto|d[eé]bito)\\s+(?:foi\\s+)?(?:aprovad[ao]|realizad[ao]|enviad[ao]|recebid[ao]|efetuad[ao]|confirmad[ao]|pag[ao])",RegexOption.IGNORE_CASE)
    private val refusalRegex=Regex("(?:^|[.\\n])\\s*(?:sua\\s+|seu\\s+)?(?:compra|transa[cç][aã]o|purchase|pix|pagamento|transfer[eê]ncia|boleto|d[eé]bito)\\s+(?:foi\\s+)?(?:negad[ao]|recusad[ao]|n[aã]o\\s+(?:foi\\s+)?(?:aprovad[ao]|realizad[ao]|conclu[ií]d[ao]|efetuad[ao])|cancelad[ao]|estornad[ao]|devolvid[ao]|agendad[ao]|declined|rejected|failed)",RegexOption.IGNORE_CASE)
    private val approvalRegex=Regex("(?:(?:compra|transa[cç][aã]o|purchase|pagamento|d[eé]bito)(?:\\s+(?:no\\s+d[eé]bito|de\\s+boleto))?\\s+(?:foi\\s+)?(?:aprovad[ao]|realizad[ao]|efetuad[ao]|confirmad[ao]|conclu[ií]d[ao]|approved|completed)|(?:pix|transfer[eê]ncia)\\s+(?:foi\\s+)?(?:enviad[ao]|recebid[ao]|realizad[ao]|efetuad[ao]|confirmad[ao]|conclu[ií]d[ao])|voc[eê]\\s+(?:enviou|fez|recebeu)\\s+(?:um\\s+|uma\\s+)?(?:pix|transfer[eê]ncia)|boleto\\s+(?:foi\\s+)?pag[ao])",RegexOption.IGNORE_CASE)
    private val incomeRegex=Regex("(?:(?:pix|transfer[eê]ncia)\\s+(?:foi\\s+)?recebid[ao]|voc[eê]\\s+recebeu\\s+(?:um\\s+|uma\\s+)?(?:pix|transfer[eê]ncia))",RegexOption.IGNORE_CASE)
    private val genericMerchantRegex=Regex("(?:compra|transa[cç][aã]o|d[eé]bito)(?:\\s+no\\s+d[eé]bito)?\\s+(?:foi\\s+)?(?:aprovad[ao]|realizad[ao]|efetuad[ao]|confirmad[ao])\\s+(?:de\\s+)?R\\$\\s*[0-9.]+,[0-9]{2}\\s+em\\s+(.+?)(?:\\.\\s|\\n|$)",RegexOption.IGNORE_CASE)
    private val counterpartyRegex=Regex("(?:pix|transfer[eê]ncia|pagamento|boleto)[^\\n]*?R\\$\\s*[0-9.]+,[0-9]{2}\\s+(?:para|de|em|a)\\s+(.+?)(?:\\.\\s|\\n|$)",RegexOption.IGNORE_CASE)
  }
  override fun onNotificationPosted(sbn:StatusBarNotification) {
    val pkg=sbn.packageName ?: return
    if(pkg !in financialPackages) return
    val notification=sbn.notification ?: return
    val extras=notification.extras ?: return
    val title=extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim().orEmpty()
    val text=extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()?.trim()
      ?.takeIf { it.isNotBlank() } ?: extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()?.trim().orEmpty()
    if(title.isBlank() && text.isBlank()) return
    val combined=listOf(title,text).filter{it.isNotBlank()}.joinToString("\n")
    if(!Regex("\\b(compra|compras|purchase|cart[aã]o|card|transa[cç][aã]o|pix|boleto|transfer[eê]ncia|pagamento|d[eé]bito)\\b",RegexOption.IGNORE_CASE).containsMatchIn(combined)) return
    val approved=approvalRegex.containsMatchIn(combined)
    val refused=refusalRegex.containsMatchIn(combined)
    if(refused&&!approved)return
    val purchaseContent=combined.split(Regex("\\n|(?<=[.!?])\\s+")).filterNot{Regex("^\\s*(?:limite|saldo)\\b",RegexOption.IGNORE_CASE).containsMatchIn(it)}.joinToString("\n")
    val amountText=amountRegex.find(purchaseContent)?.groupValues?.getOrNull(1)
    val ambiguous=!approved||refused||amountText==null||!Regex("^(?:\\d+|\\d{1,3}(?:\\.\\d{3})+),\\d{2}$").matches(amountText)
    val amount=amountText?.replace(".","")?.replace(",",".")
    val last4=Regex("(?:final|terminado|terminando|ending)\\s*(?:em)?\\s*([0-9]{4})",RegexOption.IGNORE_CASE).find(combined)?.groupValues?.getOrNull(1)
    val itauMatch=itauPurchaseRegex.find(text)
    val merchantFromSentence=itauMatch?.groupValues?.getOrNull(1)?.trim() ?: sentenceMerchantRegex.find(text)?.groupValues?.getOrNull(1)?.trim() ?: genericMerchantRegex.find(text)?.groupValues?.getOrNull(1)?.trim() ?: counterpartyRegex.find(text)?.groupValues?.getOrNull(1)?.trim() ?: counterpartyRegex.find(combined)?.groupValues?.getOrNull(1)?.trim()
    val merchant=merchantFromSentence ?: combined.replace(amountRegex,"").replace(Regex("(?:cart[aã]o\\s*)?(?:final|terminado|terminando|ending)\\s*(?:em)?\\s*[0-9]{4}",RegexOption.IGNORE_CASE),"").lines()
      .map{it.replace(Regex("(?i)(compra|transa[cç][aã]o|pix|transfer[eê]ncia|pagamento|boleto|d[eé]bito)\\s+(aprovad[ao]|realizad[ao]|efetuad[ao]|confirmad[ao]|enviad[ao]|recebid[ao]|pag[ao])"),"").trim(' ','-','|',':')}
      .firstOrNull{it.length>=3 && !Regex("cart[aã]o",RegexOption.IGNORE_CASE).containsMatchIn(it) && !genericApprovalTitleRegex.containsMatchIn(it) && listOf("nubank","ita[uú]","santander","inter","bradesco","c6 bank","mercado pago").none{bank->Regex(bank,RegexOption.IGNORE_CASE).containsMatchIn(it)}}?.take(120).orEmpty()
    val notificationTime=sbn.postTime.coerceAtLeast(0L)
    val eventTime=itauMatch?.let{match->runCatching{
      val zone=TimeZone.getTimeZone("America/Sao_Paulo");val calendar=Calendar.getInstance(zone,Locale("pt","BR"));calendar.isLenient=false;calendar.timeInMillis=notificationTime
      val rawYear=match.groupValues.getOrNull(4).orEmpty();val year=if(rawYear.isBlank())calendar.get(Calendar.YEAR) else rawYear.toInt().let{if(it<100)it+2000 else it}
      calendar.set(Calendar.YEAR,year);calendar.set(Calendar.MONTH,match.groupValues[3].toInt()-1);calendar.set(Calendar.DAY_OF_MONTH,match.groupValues[2].toInt());calendar.set(Calendar.HOUR_OF_DAY,match.groupValues[5].toInt());calendar.set(Calendar.MINUTE,match.groupValues[6].toInt());calendar.set(Calendar.SECOND,0);calendar.set(Calendar.MILLISECOND,0)
      if(rawYear.isBlank()&&calendar.timeInMillis>notificationTime+172800000L)calendar.add(Calendar.YEAR,-1)
      calendar.timeInMillis
    }.getOrNull()} ?: notificationTime
    val seed="$pkg|${sbn.key}|$notificationTime|$title|$text"
    val bytes=MessageDigest.getInstance("SHA-256").digest(seed.toByteArray())
    bytes[6]=(bytes[6].toInt() and 0x0f or 0x50).toByte(); bytes[8]=(bytes[8].toInt() and 0x3f or 0x80).toByte()
    val id=UUID.nameUUIDFromBytes(bytes).toString()
    val iso=SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",Locale.US).apply{timeZone=TimeZone.getTimeZone("UTC")}.format(Date(eventTime))
    val isIncome=!ambiguous && incomeRegex.containsMatchIn(combined)
    val paymentMethod=when {
      Regex("\\bpix\\b",RegexOption.IGNORE_CASE).containsMatchIn(combined) -> "pix"
      Regex("\\bboleto\\b",RegexOption.IGNORE_CASE).containsMatchIn(combined) -> "boleto"
      Regex("\\btransfer[eê]ncia\\b",RegexOption.IGNORE_CASE).containsMatchIn(combined) -> "transferencia"
      Regex("\\bd[eé]bito\\b",RegexOption.IGNORE_CASE).containsMatchIn(combined) -> "cartao_debito"
      else -> null
    }
    val eventType=when {
      ambiguous -> "financial_notification"
      isIncome -> "manual_income"
      else -> "card_purchase"
    }
    val parsed=JSONObject().put("currency","BRL").put("event_type",eventType)
    if(!ambiguous){
      parsed.put("direction",if(isIncome)"income" else "expense")
      if(amount!=null)runCatching{parsed.put("amount",BigDecimal(amount))}
      if(merchant.isNotBlank())parsed.put("merchant_raw",merchant)
      if(paymentMethod!=null)parsed.put("payment_method",paymentMethod)
    }
    if(last4!=null) parsed.put("payment_instrument_hint",last4)
    parsed.put("event_timestamp",iso)
    val event=JSONObject().put("event_id",id).put("source_type","android_notification").put("source_provider",pkg)
      .put("captured_at",iso).put("raw_payload",JSONObject().put("title",title).put("text",text).put("package",pkg).put("origin_verification","exact_package_allowlist").put("notification_key",sbn.key))
      .put("parsed_payload",parsed)
    val prefs=getSharedPreferences(PREFS,MODE_PRIVATE)
    try{NotificationInbox.append(prefs,event)}catch(_:Exception){android.util.Log.e("FinancialTwin","Notification queue persistence failed; original storage retained")}
  }
}
