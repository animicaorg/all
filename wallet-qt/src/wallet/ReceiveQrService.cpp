#include "ReceiveQrService.h"

#include "AnimicaWalletBackend.h"

#include <QByteArray>
#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QRegularExpression>
#include <QSaveFile>
#include <QUrl>
#include <QUrlQuery>

namespace {

constexpr int QR_RENDER_TIMEOUT_MS = 15000;
const QRegularExpression kAmountPattern(QStringLiteral("^\\d+(?:\\.\\d{0,9})?$"));

QStringList existingPyPath(const QProcessEnvironment& env)
{
    const QString separator =
#ifdef Q_OS_WIN
        QStringLiteral(";");
#else
        QStringLiteral(":");
#endif
    return env.value(QStringLiteral("PYTHONPATH")).split(separator, Qt::SkipEmptyParts);
}

QString joinPyPath(const QStringList& paths)
{
#ifdef Q_OS_WIN
    return paths.join(QLatin1Char(';'));
#else
    return paths.join(QLatin1Char(':'));
#endif
}

} // namespace

ReceiveQrResult ReceiveQrService::generate(const ReceiveQrRequest& request) const
{
    const QString address = request.address.trimmed();
    if (address.isEmpty()) {
        return makeFailure(ReceiveQrResult::Status::EmptyAddress,
                           QStringLiteral("Select a wallet to generate a receive QR."),
                           QStringLiteral("The receive screen does not have an address to encode yet."));
    }

    bool amountOk = false;
    const QString normalizedAmount = normalizeAmount(request.amount, &amountOk);
    if (!amountOk) {
        return makeFailure(ReceiveQrResult::Status::InvalidAmount,
                           QStringLiteral("Enter a valid amount to include it in the QR."),
                           QStringLiteral("Amounts must be decimal ANM values with up to 9 fractional digits."));
    }

    ReceiveQrRequest normalizedRequest = request;
    normalizedRequest.address = address;
    normalizedRequest.amount = normalizedAmount;
    normalizedRequest.message = request.message.trimmed();

    const QString payload = buildPayload(normalizedRequest);
    const QString python = resolvePythonInterpreter();
    if (python.isEmpty()) {
        return makeFailure(ReceiveQrResult::Status::PythonUnavailable,
                           QStringLiteral("QR renderer unavailable in this build."),
                           QStringLiteral("No Python interpreter was found for the bundled wallet runtime."),
                           payload);
    }

    return runPythonRenderer(python, payload, normalizedRequest.pixelSize);
}

QString ReceiveQrService::buildPayload(const ReceiveQrRequest& request)
{
    const QString address = request.address.trimmed();
    if (address.isEmpty()) {
        return QString();
    }

    QString payload = QStringLiteral("animica:%1").arg(address);
    QUrlQuery query;
    if (!request.amount.trimmed().isEmpty()) {
        query.addQueryItem(QStringLiteral("amount"), request.amount.trimmed());
    }
    if (!request.message.trimmed().isEmpty()) {
        query.addQueryItem(QStringLiteral("memo"), request.message.trimmed());
    }

    const QString queryString = query.toString(QUrl::FullyEncoded);
    if (!queryString.isEmpty()) {
        payload += QLatin1Char('?');
        payload += queryString;
    }
    return payload;
}

QString ReceiveQrService::normalizeAmount(const QString& amount, bool* ok)
{
    const QString trimmed = amount.trimmed();
    if (trimmed.isEmpty()) {
        if (ok) {
            *ok = true;
        }
        return QString();
    }

    if (!kAmountPattern.match(trimmed).hasMatch()) {
        if (ok) {
            *ok = false;
        }
        return QString();
    }

    const QStringList parts = trimmed.split(QLatin1Char('.'));
    QString whole = parts.value(0);
    QString fractional = parts.value(1);

    while (whole.size() > 1 && whole.startsWith(QLatin1Char('0'))) {
        whole.remove(0, 1);
    }

    while (!fractional.isEmpty() && fractional.endsWith(QLatin1Char('0'))) {
        fractional.chop(1);
    }

    if (whole.isEmpty()) {
        whole = QStringLiteral("0");
    }

    if (ok) {
        *ok = true;
    }

    if (fractional.isEmpty()) {
        return whole;
    }
    return whole + QLatin1Char('.') + fractional;
}

bool ReceiveQrService::savePng(const QImage& image, const QString& filePath, QString* errorMessage)
{
    if (image.isNull()) {
        if (errorMessage) {
            *errorMessage = QStringLiteral("No QR image is available to save.");
        }
        return false;
    }

    QSaveFile output(filePath);
    if (!output.open(QIODevice::WriteOnly)) {
        if (errorMessage) {
            *errorMessage = output.errorString();
        }
        return false;
    }

    if (!image.save(&output, "PNG")) {
        if (errorMessage) {
            *errorMessage = QStringLiteral("Qt could not encode the QR image as PNG.");
        }
        output.cancelWriting();
        return false;
    }

    if (!output.commit()) {
        if (errorMessage) {
            *errorMessage = output.errorString();
        }
        return false;
    }
    return true;
}

QString ReceiveQrService::resolvePythonInterpreter() const
{
    return AnimicaWalletBackend::findPythonInterpreter();
}

QProcessEnvironment ReceiveQrService::baseEnvironment() const
{
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString repoRoot = AnimicaWalletBackend::findRepoRoot();
    if (!repoRoot.isEmpty()) {
        QStringList pyPath = existingPyPath(env);
        pyPath.prepend(QDir(repoRoot).filePath("sdk/python"));
        pyPath.prepend(QDir(repoRoot).filePath("python"));
        env.insert(QStringLiteral("PYTHONPATH"), joinPyPath(pyPath));
    }
    return env;
}

ReceiveQrResult ReceiveQrService::runPythonRenderer(const QString& python,
                                                    const QString& payload,
                                                    int pixelSize) const
{
    QProcess process;
    process.setProcessEnvironment(baseEnvironment());

    process.start(python, {QStringLiteral("-m"), QStringLiteral("animica.wallet_qr")});
    if (!process.waitForStarted(5000)) {
        return makeFailure(ReceiveQrResult::Status::RenderingFailed,
                           QStringLiteral("Failed to start the QR renderer."),
                           process.errorString(),
                           payload);
    }

    const QJsonObject request{
        {QStringLiteral("payload"), payload},
        {QStringLiteral("pixel_size"), qMax(128, pixelSize)},
    };
    process.write(QJsonDocument(request).toJson(QJsonDocument::Compact));
    process.closeWriteChannel();

    if (!process.waitForFinished(QR_RENDER_TIMEOUT_MS)) {
        process.kill();
        process.waitForFinished(1000);
        return makeFailure(ReceiveQrResult::Status::RenderingFailed,
                           QStringLiteral("Timed out while generating the QR image."),
                           QStringLiteral("The QR helper process did not return a PNG within the expected timeout."),
                           payload);
    }

    const QByteArray stdoutData = process.readAllStandardOutput().trimmed();
    const QByteArray stderrData = process.readAllStandardError().trimmed();
    QJsonParseError parseError;
    const QJsonDocument response = QJsonDocument::fromJson(stdoutData, &parseError);
    if (parseError.error != QJsonParseError::NoError || !response.isObject()) {
        const QString detail = !stderrData.isEmpty()
            ? QString::fromUtf8(stderrData)
            : QString::fromUtf8(stdoutData);
        return makeFailure(ReceiveQrResult::Status::RenderingFailed,
                           QStringLiteral("The QR renderer returned an invalid response."),
                           detail,
                           payload);
    }

    const QJsonObject object = response.object();
    if (!object.value(QStringLiteral("ok")).toBool()) {
        const QString errorKind = object.value(QStringLiteral("error_kind")).toString();
        const ReceiveQrResult::Status status =
            errorKind == QStringLiteral("dependency_missing")
                ? ReceiveQrResult::Status::DependencyMissing
                : ReceiveQrResult::Status::RenderingFailed;
        return makeFailure(status,
                           object.value(QStringLiteral("error_summary"))
                               .toString(QStringLiteral("QR generation failed.")),
                           object.value(QStringLiteral("error_details")).toString(),
                           payload);
    }

    const QByteArray pngBytes = QByteArray::fromBase64(
        object.value(QStringLiteral("png_base64")).toString().toLatin1());
    QImage image;
    if (pngBytes.isEmpty() || !image.loadFromData(pngBytes, "PNG")) {
        return makeFailure(ReceiveQrResult::Status::RenderingFailed,
                           QStringLiteral("The QR renderer did not return a valid PNG."),
                           QStringLiteral("The helper process completed, but the image payload could not be decoded."),
                           payload);
    }

    ReceiveQrResult result;
    result.status = ReceiveQrResult::Status::Success;
    result.payload = payload;
    result.image = image;
    return result;
}

ReceiveQrResult ReceiveQrService::makeFailure(ReceiveQrResult::Status status,
                                              const QString& summary,
                                              const QString& details,
                                              const QString& payload)
{
    ReceiveQrResult result;
    result.status = status;
    result.payload = payload;
    result.errorSummary = summary;
    result.errorDetails = details;
    return result;
}
