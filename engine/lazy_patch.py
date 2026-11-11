"""No-op (dipertahankan hanya agar workflow lama yang memanggil file ini tidak gagal).
Patch versi lama sudah tidak relevan; app.js v12 sudah memuat marker yang dibutuhkan."""


def main():
    print("lazy_patch: tidak ada yang perlu dipatch (v12)")


if __name__ == "__main__":
    main()
