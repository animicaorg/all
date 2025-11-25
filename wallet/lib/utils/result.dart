/// Result<E, T> — lightweight success/error container with helpers.
/// Dart 3+ (uses sealed classes). No external deps.
///
/// Example:
///   Result<String, int> parseInt(String s) {
///     return Result.trySync(
///       () => int.parse(s),
///       mapError: (e, _) => 'bad-int',
///     );
///   }
///   final r = parseInt('42')
///       .map((n) => n * 2)
///       .andThen((n) => n > 50 ? Result.err('too-big') : Result.ok(n));
///
///   r.fold(
///     onOk: (v) => print('ok: $v'),
///     onErr: (e) => print('err: $e'),
///   );
sealed class Result<E, T> {
  const Result();

  /// Construct an OK value.
  factory Result.ok(T value) = Ok<E, T>;

  /// Construct an ERR value.
  factory Result.err(E error) = Err<E, T>;

  bool get isOk => this is Ok<E, T>;
  bool get isErr => this is Err<E, T>;

  /// Extract the value or throw if Err.
  T unwrap() => switch (this) {
        Ok<E, T>(:final value) => value,
        Err<E, T>(:final error) =>
          throw StateError('Tried to unwrap Err($error)'),
      };

  /// Extract the error or throw if Ok.
  E unwrapErr() => switch (this) {
        Err<E, T>(:final error) => error,
        Ok<E, T>(:final value) =>
          throw StateError('Tried to unwrapOk on Ok($value)'),
      };

  /// Value or default.
  T unwrapOr(T fallback) => switch (this) {
        Ok<E, T>(:final value) => value,
        Err<E, T>() => fallback,
      };

  /// Value or computed from error.
  T unwrapOrElse(T Function(E) orElse) => switch (this) {
        Ok<E, T>(:final value) => value,
        Err<E, T>(:final error) => orElse(error),
      };

  /// Pattern-like fold.
  R fold<R>({required R Function(T) onOk, required R Function(E) onErr}) =>
      switch (this) {
        Ok<E, T>(:final value) => onOk(value),
        Err<E, T>(:final error) => onErr(error),
      };

  /// Map the success value.
  Result<E, R> map<R>(R Function(T) f) => switch (this) {
        Ok<E, T>(:final value) => Ok<E, R>(f(value)),
        Err<E, T>(:final error) => Err<E, R>(error),
      };

  /// Map the error.
  Result<F, T> mapErr<F>(F Function(E) f) => switch (this) {
        Ok<E, T>(:final value) => Ok<F, T>(value),
        Err<E, T>(:final error) => Err<F, T>(f(error)),
      };

  /// Flat-map (andThen).
  Result<E, R> andThen<R>(Result<E, R> Function(T) f) => switch (this) {
        Ok<E, T>(:final value) => f(value),
        Err<E, T>(:final error) => Err<E, R>(error),
      };

  /// Side-effect on Ok.
  Result<E, T> tap(void Function(T) side) {
    if (this is Ok<E, T>) side((this as Ok<E, T>).value);
    return this;
  }

  /// Side-effect on Err.
  Result<E, T> tapErr(void Function(E) side) {
    if (this is Err<E, T>) side((this as Err<E, T>).error);
    return this;
  }

  /// Convert to nullable (drops error).
  T? toNullable() => switch (this) {
        Ok<E, T>(:final value) => value,
        Err<E, T>() => null,
      };

  /// Convert to Future, rejecting on error.
  Future<T> toFuture() => switch (this) {
        Ok<E, T>(:final value) => Future.value(value),
        Err<E, T>(:final error) => Future.error(error),
      };

  /// Try/catch wrapper (sync). Map any thrown error with [mapError].
  static Result<E, T> trySync<E, T>(
    T Function() body, {
    required E Function(Object, StackTrace) mapError,
  }) {
    try {
      return Result.ok(body());
    } catch (e, st) {
      return Result.err(mapError(e, st));
    }
  }

  /// Try/catch wrapper (async). Map any thrown error with [mapError].
  static Future<Result<E, T>> tryAsync<E, T>(
    Future<T> Function() body, {
    required E Function(Object, StackTrace) mapError,
  }) async {
    try {
      final v = await body();
      return Result.ok(v);
    } catch (e, st) {
      return Result.err(mapError(e, st));
    }
  }

  /// From nullable (Err if null).
  static Result<E, T> fromNullable<E, T>(T? v, {required E Function() err}) =>
      v == null ? Result.err(err()) : Result.ok(v);

  /// From boolean guard.
  static Result<E, T> fromBool<E, T>(
    bool ok, {
    required T Function() value,
    required E Function() error,
  }) =>
      ok ? Result.ok(value()) : Result.err(error());

  /// Sequence: turn List<Result<E,T>> into Result<E,List<T>> (fail fast).
  static Result<E, List<T>> sequence<E, T>(Iterable<Result<E, T>> items) {
    final out = <T>[];
    for (final r in items) {
      switch (r) {
        case Ok<E, T>(:final value):
          out.add(value);
        case Err<E, T>(:final error):
          return Result.err(error);
      }
    }
    return Result.ok(out);
  }

  /// Combine two results (zip).
  static Result<E, (A, B)> zip2<E, A, B>(
    Result<E, A> ra,
    Result<E, B> rb,
  ) =>
      switch ((ra, rb)) {
        (Ok<E, A>(:final value), Ok<E, B>(:final value: final vb)) => Result.ok((value, vb)),
        (Err<E, A>(:final error), _) => Result.err(error),
        (_, Err<E, B>(:final error)) => Result.err(error),
      };
}

final class Ok<E, T> extends Result<E, T> {
  final T value;
  const Ok(this.value);
  @override
  String toString() => 'Ok($value)';
}

final class Err<E, T> extends Result<E, T> {
  final E error;
  const Err(this.error);
  @override
  String toString() => 'Err($error)';
}

/// Type alias for async results.
typedef AsyncResult<E, T> = Future<Result<E, T>>;

/// Extensions for chaining AsyncResult without nesting.
extension AsyncResultExt<E, T> on AsyncResult<E, T> {
  Future<Result<E, R>> mapAsync<R>(Future<R> Function(T) f) async {
    final r = await this;
    return r.andThen<Result<E, R>>((v) async {
      final nv = await f(v);
      return Result.ok(nv);
    });
  }

  Future<Result<E, R>> andThenAsync<R>(
      Future<Result<E, R>> Function(T) f) async {
    final r = await this;
    return switch (r) {
      Ok<E, T>(:final value) => await f(value),
      Err<E, T>(:final error) => Result.err(error),
    };
  }

  Future<T> unwrapOrAsync(T Function(E) orElse) async {
    final r = await this;
    return r.unwrapOrElse(orElse);
  }

  Future<R> foldAsync<R>({
    required Future<R> Function(T) onOk,
    required Future<R> Function(E) onErr,
  }) async {
    final r = await this;
    return switch (r) {
      Ok<E, T>(:final value) => await onOk(value),
      Err<E, T>(:final error) => await onErr(error),
    };
  }
}

/// Convenience extensions to lift raw values into Result.
extension ResultOkLift<T> on T {
  Result<E, T> toOk<E>() => Result.ok(this);
}

extension ResultErrLift<E> on E {
  Result<E, T> toErr<T>() => Result.err(this);
}
