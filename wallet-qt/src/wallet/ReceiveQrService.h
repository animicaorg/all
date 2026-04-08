#ifndef RECEIVEQRSERVICE_H
#define RECEIVEQRSERVICE_H

#include <QImage>
#include <QProcessEnvironment>
#include <QString>

struct ReceiveQrRequest
{
    QString address;
    QString amount;
    QString message;
    int pixelSize = 512;
};

struct ReceiveQrResult
{
    enum class Status {
        Success,
        EmptyAddress,
        InvalidAmount,
        PythonUnavailable,
        DependencyMissing,
        RenderingFailed,
    };

    Status status = Status::RenderingFailed;
    QString payload;
    QString errorSummary;
    QString errorDetails;
    QImage image;

    bool isSuccess() const
    {
        return status == Status::Success && !image.isNull();
    }
};

class ReceiveQrService
{
public:
    virtual ~ReceiveQrService() = default;

    virtual ReceiveQrResult generate(const ReceiveQrRequest& request) const;

    static QString buildPayload(const ReceiveQrRequest& request);
    static QString normalizeAmount(const QString& amount, bool* ok = nullptr);
    static bool savePng(const QImage& image, const QString& filePath, QString* errorMessage = nullptr);

protected:
    virtual QString resolvePythonInterpreter() const;
    virtual QProcessEnvironment baseEnvironment() const;

private:
    ReceiveQrResult runPythonRenderer(const QString& python,
                                      const QString& payload,
                                      int pixelSize) const;
    static ReceiveQrResult makeFailure(ReceiveQrResult::Status status,
                                       const QString& summary,
                                       const QString& details = QString(),
                                       const QString& payload = QString());
};

#endif // RECEIVEQRSERVICE_H
