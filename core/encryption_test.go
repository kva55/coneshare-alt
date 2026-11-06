package main

import (
	"bytes"
	"crypto/rand"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const testKey = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

func newTestCipher(t *testing.T) *FileCipher {
	t.Helper()
	c, err := NewFileCipher(testKey)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func sealToFile(t *testing.T, c *FileCipher, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "f.bin")
	f, _ := os.Create(path)
	w := c.NewWriter(f)
	if _, err := w.Write(data); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	f.Close()
	return path
}

func TestRoundTripAcrossChunkBoundaries(t *testing.T) {
	c := newTestCipher(t)
	for _, n := range []int{0, 1, encChunkSize - 1, encChunkSize, encChunkSize + 1, 3*encChunkSize + 17} {
		data := make([]byte, n)
		_, _ = rand.Read(data)
		path := sealToFile(t, c, data)
		r, size, err := c.Open(path)
		if err != nil {
			t.Fatalf("n=%d: %v", n, err)
		}
		got, err := io.ReadAll(r)
		r.Close()
		if err != nil || !bytes.Equal(got, data) || size != int64(n) {
			t.Fatalf("n=%d: round trip failed (err=%v size=%d len=%d)", n, err, size, len(got))
		}
	}
}

func TestStoredFileDoesNotContainThePlaintext(t *testing.T) {
	c := newTestCipher(t)
	path := sealToFile(t, c, []byte(strings.Repeat("TOP-SECRET ", 20000)))
	raw, _ := os.ReadFile(path)
	if bytes.Contains(raw, []byte("TOP-SECRET")) || !bytes.HasPrefix(raw, []byte(encMagic)) {
		t.Fatal("stored file is not encrypted")
	}
}

func TestSeekReadsTheRightBytes(t *testing.T) {
	c := newTestCipher(t)
	data := make([]byte, 5*encChunkSize)
	_, _ = rand.Read(data)
	r, _, _ := c.Open(sealToFile(t, c, data))
	defer r.Close()
	off := int64(2*encChunkSize - 10)
	if _, err := r.Seek(off, io.SeekStart); err != nil {
		t.Fatal(err)
	}
	got := make([]byte, 100)
	if _, err := io.ReadFull(r, got); err != nil || !bytes.Equal(got, data[off:off+100]) {
		t.Fatal("seek across a chunk boundary returned the wrong bytes")
	}
}

func TestTamperingAndTruncationAreDetected(t *testing.T) {
	c := newTestCipher(t)
	data := make([]byte, 3*encChunkSize)
	path := sealToFile(t, c, data)
	raw, _ := os.ReadFile(path)

	flipped := append([]byte(nil), raw...)
	flipped[encHeaderSize+100] ^= 1
	_ = os.WriteFile(path, flipped, 0o600)
	r, _, err := c.Open(path)
	if err == nil {
		_, err = io.ReadAll(r)
		r.Close()
	}
	if err == nil {
		t.Fatal("a changed byte was not detected")
	}

	// drop the last chunk: the new last chunk was not sealed as final
	_ = os.WriteFile(path, raw[:len(raw)-encSealedFull], 0o600)
	r, _, err = c.Open(path)
	if err == nil {
		_, err = io.ReadAll(r)
		r.Close()
	}
	if err == nil {
		t.Fatal("a truncated file was not detected")
	}
}

func TestWrongKeyCannotRead(t *testing.T) {
	c := newTestCipher(t)
	path := sealToFile(t, c, []byte("hello"))
	other, _ := NewFileCipher(strings.Repeat("ab", 32))
	r, _, err := other.Open(path)
	if err == nil {
		_, err = io.ReadAll(r)
		r.Close()
	}
	if err == nil {
		t.Fatal("a different key opened the file")
	}
}

func TestPlaintextFilesAreServedUntilConverted(t *testing.T) {
	c := newTestCipher(t)
	dir := t.TempDir()
	path := filepath.Join(dir, "old.txt")
	_ = os.WriteFile(path, []byte("legacy"), 0o600)
	r, size, err := c.Open(path)
	if err != nil || size != 6 {
		t.Fatal(err)
	}
	r.Close()
	n, err := c.EncryptExisting(dir)
	if err != nil || n != 1 {
		t.Fatalf("converted %d: %v", n, err)
	}
	raw, _ := os.ReadFile(path)
	if bytes.Contains(raw, []byte("legacy")) {
		t.Fatal("file was not encrypted")
	}
	if n, _ := c.EncryptExisting(dir); n != 0 {
		t.Fatal("an already encrypted file was converted again")
	}
	r, _, _ = c.Open(path)
	got, _ := io.ReadAll(r)
	r.Close()
	if string(got) != "legacy" {
		t.Fatal("converted file does not read back")
	}
}

func TestKeyValidation(t *testing.T) {
	for _, bad := range []string{"", "short", strings.Repeat("zz", 32), strings.Repeat("ab", 31)} {
		if _, err := NewFileCipher(bad); err == nil {
			t.Fatalf("accepted %q", bad)
		}
	}
}
