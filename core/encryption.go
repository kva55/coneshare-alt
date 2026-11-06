package main

// Encryption of stored files at rest.
//
// Every file written by the file service is sealed with AES-256-GCM in 64 KiB chunks, so that large
// files can be streamed and range requests (video, PDF) can seek without decrypting the whole file.
//
//	file   = "CSE1" | nonce prefix (8 random bytes) | sealed chunk 0 | sealed chunk 1 | ...
//	nonce  = prefix | chunk index (uint32, big endian)
//	AAD    = 0x01 for the last chunk, 0x00 for all others (so cutting a file short is detected)
//
// The key is a 32-byte secret from the environment (STORAGE_ENCRYPTION_KEY, 64 hex characters).
// It is never stored beside the data. Without it the files on disk, in backups and in volume
// snapshots are unreadable. Files written before encryption was switched on carry no "CSE1"
// header and are served as they are until `core -encrypt-existing` converts them.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

const (
	encChunkSize  = 64 * 1024
	encTagSize    = 16
	encMagic      = "CSE1"
	encPrefixSize = 8
	encHeaderSize = len(encMagic) + encPrefixSize
	encSealedFull = encChunkSize + encTagSize
)

// FileCipher seals and opens stored files. A nil *FileCipher means encryption is off.
type FileCipher struct {
	aead cipher.AEAD
}

func NewFileCipher(hexKey string) (*FileCipher, error) {
	key, err := hex.DecodeString(strings.TrimSpace(hexKey))
	if err != nil || len(key) != 32 {
		return nil, errors.New("STORAGE_ENCRYPTION_KEY must be 64 hexadecimal characters (generate one with: openssl rand -hex 32)")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &FileCipher{aead: aead}, nil
}

func chunkNonce(prefix []byte, index uint32) []byte {
	nonce := make([]byte, 12)
	copy(nonce, prefix)
	binary.BigEndian.PutUint32(nonce[encPrefixSize:], index)
	return nonce
}

func finalFlag(final bool) []byte {
	if final {
		return []byte{1}
	}
	return []byte{0}
}

type encWriter struct {
	dst    io.Writer
	c      *FileCipher
	prefix []byte
	buf    []byte
	index  uint32
	begun  bool
}

// NewWriter returns a writer that encrypts into dst. Close must be called to write the last chunk.
func (c *FileCipher) NewWriter(dst io.Writer) io.WriteCloser {
	return &encWriter{dst: dst, c: c, buf: make([]byte, 0, encChunkSize)}
}

func (w *encWriter) begin() error {
	if w.begun {
		return nil
	}
	w.prefix = make([]byte, encPrefixSize)
	if _, err := rand.Read(w.prefix); err != nil {
		return err
	}
	w.begun = true
	_, err := w.dst.Write(append([]byte(encMagic), w.prefix...))
	return err
}

func (w *encWriter) flush(final bool) error {
	if err := w.begin(); err != nil {
		return err
	}
	sealed := w.c.aead.Seal(nil, chunkNonce(w.prefix, w.index), w.buf, finalFlag(final))
	if _, err := w.dst.Write(sealed); err != nil {
		return err
	}
	w.index++
	w.buf = w.buf[:0]
	return nil
}

func (w *encWriter) Write(p []byte) (int, error) {
	total := len(p)
	for len(p) > 0 {
		if len(w.buf) == encChunkSize { // more data follows, so this chunk is not the last
			if err := w.flush(false); err != nil {
				return 0, err
			}
		}
		k := encChunkSize - len(w.buf)
		if k > len(p) {
			k = len(p)
		}
		w.buf = append(w.buf, p[:k]...)
		p = p[k:]
	}
	return total, nil
}

func (w *encWriter) Close() error { return w.flush(true) }

// isEncrypted reports whether the file starts with the encryption header.
func isEncrypted(f *os.File) bool {
	head := make([]byte, len(encMagic))
	n, _ := f.ReadAt(head, 0)
	return n == len(encMagic) && string(head) == encMagic
}

// plainSize is the size of the plaintext of an encrypted file of the given stored size.
func plainSize(stored int64) (int64, error) {
	body := stored - int64(encHeaderSize)
	if body < encTagSize {
		return 0, errors.New("encrypted file is too short")
	}
	full, rem := body/int64(encSealedFull), body%int64(encSealedFull)
	size := full * encChunkSize
	if rem > 0 {
		if rem < encTagSize {
			return 0, errors.New("encrypted file is truncated")
		}
		size += rem - encTagSize
	}
	return size, nil
}

type decReader struct {
	f      *os.File
	c      *FileCipher
	prefix []byte
	stored int64
	size   int64
	pos    int64
	cached int64 // index of the chunk in buf, -1 if none
	buf    []byte
}

// Open returns a seekable reader over the plaintext of path. A file without the encryption header
// (stored before encryption was switched on) is returned as it is.
func (c *FileCipher) Open(path string) (io.ReadSeekCloser, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, 0, err
	}
	if c == nil || !isEncrypted(f) {
		return f, st.Size(), nil
	}
	prefix := make([]byte, encPrefixSize)
	if _, err := f.ReadAt(prefix, int64(len(encMagic))); err != nil {
		f.Close()
		return nil, 0, errors.New("encrypted file has a damaged header")
	}
	size, err := plainSize(st.Size())
	if err != nil {
		f.Close()
		return nil, 0, err
	}
	return &decReader{f: f, c: c, prefix: prefix, stored: st.Size(), size: size, cached: -1}, size, nil
}

func (r *decReader) lastIndex() int64 {
	if r.size == 0 {
		return 0
	}
	return (r.size - 1) / encChunkSize
}

func (r *decReader) load(index int64) error {
	if r.cached == index {
		return nil
	}
	offset := int64(encHeaderSize) + index*int64(encSealedFull)
	n := r.stored - offset
	if n > int64(encSealedFull) {
		n = int64(encSealedFull)
	}
	if n < encTagSize {
		return errors.New("encrypted file is truncated")
	}
	sealed := make([]byte, n)
	if _, err := r.f.ReadAt(sealed, offset); err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	plain, err := r.c.aead.Open(nil, chunkNonce(r.prefix, uint32(index)), sealed, finalFlag(index == r.lastIndex()))
	if err != nil {
		return fmt.Errorf("stored file failed authentication (wrong key, or the file was changed): %w", err)
	}
	r.buf, r.cached = plain, index
	return nil
}

func (r *decReader) Read(p []byte) (int, error) {
	if r.pos >= r.size {
		return 0, io.EOF
	}
	index := r.pos / encChunkSize
	if err := r.load(index); err != nil {
		return 0, err
	}
	n := copy(p, r.buf[r.pos-index*encChunkSize:])
	r.pos += int64(n)
	return n, nil
}

func (r *decReader) Seek(offset int64, whence int) (int64, error) {
	var abs int64
	switch whence {
	case io.SeekStart:
		abs = offset
	case io.SeekCurrent:
		abs = r.pos + offset
	case io.SeekEnd:
		abs = r.size + offset
	default:
		return 0, errors.New("invalid whence")
	}
	if abs < 0 {
		return 0, errors.New("negative position")
	}
	r.pos = abs
	return abs, nil
}

func (r *decReader) Close() error { return r.f.Close() }

// storedPlainSize is the plaintext size of a stored file (its size on disk if it is not encrypted).
func (c *FileCipher) storedPlainSize(path string, stored int64) int64 {
	if c == nil {
		return stored
	}
	f, err := os.Open(path)
	if err != nil {
		return stored
	}
	defer f.Close()
	if !isEncrypted(f) {
		return stored
	}
	if size, err := plainSize(stored); err == nil {
		return size
	}
	return stored
}

// EncryptExisting seals every plaintext file below root in place (write a sealed copy, then rename).
// Files already sealed, and unfinished uploads (".part-"), are left alone. Returns how many were converted.
func (c *FileCipher) EncryptExisting(root string) (int, error) {
	converted := 0
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || !info.Mode().IsRegular() || strings.Contains(filepath.Base(path), ".part-") {
			return err
		}
		src, err := os.Open(path)
		if err != nil {
			return err
		}
		if isEncrypted(src) {
			src.Close()
			return nil
		}
		tmp := path + ".part-encrypt"
		dst, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, info.Mode().Perm())
		if err != nil {
			src.Close()
			return err
		}
		w := c.NewWriter(dst)
		_, copyErr := io.Copy(w, src)
		closeErr := w.Close()
		src.Close()
		syncErr := dst.Sync()
		dst.Close()
		for _, e := range []error{copyErr, closeErr, syncErr} {
			if e != nil {
				os.Remove(tmp)
				return e
			}
		}
		if err := os.Rename(tmp, path); err != nil {
			os.Remove(tmp)
			return err
		}
		converted++
		return nil
	})
	return converted, err
}
