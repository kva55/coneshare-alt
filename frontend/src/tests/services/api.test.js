import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import api from "../../services/api";
import {
  createOwnerQnaMessage,
  createOwnerQnaThread,
  createPublicQnaMessage,
  createPublicQnaThread,
  getOwnerQnaThreads,
  getPublicQnaMessages,
  getPublicQnaSummary,
  getPublicQnaThreads,
  getShareLinkViewData,
  updateOwnerQnaThreadStatus,
  downloadDataroomFolder,
  downloadDataroomArchive,
  fetchSharePageBlob,
  fetchDocumentPageBlob,
} from "../../services/api";
import { DATAROOM_VIEWER_PAGE_SIZE } from "../../constants/pagination";
import axios from "axios";
import { toast } from "sonner";

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

// Mock axios. The factory ensures that when api.js calls axios.create(), it gets
// a real axios instance, while the top-level axios.post used for token
// refresh remains a separate mock function.
vi.mock("axios", async (importOriginal) => {
  const actualAxios = await importOriginal();
  const mockCreatedInstance = actualAxios.default.create();

  return {
    default: {
      create: () => mockCreatedInstance,
      post: vi.fn(),
    },
  };
});

describe("API Service Interceptors", () => {
  let mockAdapter;
  let originalWindowLocation;

  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();

    // Spy on localStorage to track calls
    vi.spyOn(Storage.prototype, "removeItem");

    // Mock the adapter for the 'api' instance to control its responses
    mockAdapter = vi.fn();
    api.defaults.adapter = mockAdapter;

    // Mock window.location for redirection tests
    originalWindowLocation = window.location;
    delete window.location;
    window.location = {
      href: "",
    };
  });

  afterEach(() => {
    window.location = originalWindowLocation;
  });

  describe("Response Interceptor", () => {
    it("should not redirect to login for wrong share-link password (public flow 401)", async () => {
      const originalError = {
        response: {
          status: 401,
          data: { message: "Invalid password.", protectionType: "password" },
        },
        config: { url: "/links/test-slug/verify-password/" },
      };
      mockAdapter.mockRejectedValue(originalError);

      await expect(api.post("/links/test-slug/verify-password/", { password: "wrong" })).rejects.toBe(originalError);

      expect(axios.post).not.toHaveBeenCalled();
      expect(window.location.href).toBe("");
    });

    it("should not refresh or redirect for any 401 carrying public protectionType", async () => {
      const originalError = {
        response: {
          status: 401,
          data: { message: "Password required", protectionType: "password" },
        },
        config: { url: "/links/test-slug/any-public-endpoint/" },
      };
      mockAdapter.mockRejectedValue(originalError);

      await expect(api.get("/links/test-slug/any-public-endpoint/")).rejects.toBe(originalError);

      expect(axios.post).not.toHaveBeenCalled();
      expect(window.location.href).toBe("");
    });

    it.each(["password", "email", "nda"])(
      "should suppress error toast on /view-data/ when 401 protectionType is %s",
      async (protectionType) => {
        const originalError = {
          response: {
            status: 401,
            data: { message: `${protectionType} required`, protectionType },
          },
          config: { url: "/links/test-slug/view-data/" },
        };
        mockAdapter.mockRejectedValue(originalError);

        await expect(api.get("/links/test-slug/view-data/")).rejects.toBe(originalError);

        expect(toast.error).not.toHaveBeenCalled();
        expect(axios.post).not.toHaveBeenCalled();
        expect(window.location.href).toBe("");
      }
    );

    it("should display error toast for 401 on non-view-data endpoints (e.g. invalid password)", async () => {
      const originalError = {
        response: {
          status: 401,
          data: { message: "Invalid password.", protectionType: "password" },
        },
        config: { url: "/links/test-slug/verify-password/" },
      };
      mockAdapter.mockRejectedValue(originalError);

      await expect(api.post("/links/test-slug/verify-password/", { password: "wrong" })).rejects.toBe(originalError);

      expect(toast.error).toHaveBeenCalledWith("Invalid password.");
    });

    it("should parse blob response error containing JSON protectionType and prevent redirect to /login", async () => {
      const errorJson = JSON.stringify({ detail: "Password required", protectionType: "password" });
      const blob = new Blob([errorJson], { type: "application/json" });

      const originalError = {
        response: {
          status: 401,
          data: blob,
        },
        config: { url: "/links/test-slug/download-folder/f1/" },
      };
      mockAdapter.mockRejectedValue(originalError);

      let caught;
      try {
        await api.get("/links/test-slug/download-folder/f1/", { responseType: "blob" });
      } catch (err) {
        caught = err;
      }

      expect(window.location.href).toBe("");
      expect(caught.response.data.protectionType).toBe("password");
      expect(axios.post).not.toHaveBeenCalled();
    });

    it("should parse blob response error detail on 403 and display translated error toast", async () => {
      const errorJson = JSON.stringify({ detail: "You do not have permission to view this document." });
      const blob = new Blob([errorJson], { type: "application/json" });

      const originalError = {
        response: {
          status: 403,
          data: blob,
        },
        config: { url: "/links/test-slug/download-folder/f1/" },
      };
      mockAdapter.mockRejectedValue(originalError);

      await expect(api.get("/links/test-slug/download-folder/f1/", { responseType: "blob" })).rejects.toBe(originalError);

      expect(originalError.response.data.detail).toBe("You do not have permission to view this document.");
      expect(toast.error).toHaveBeenCalled();
    });

  });

  describe("Cookie session handling", () => {
    const setCsrfCookie = (value) => {
      document.cookie = `csrftoken=${value}; path=/`;
    };
    afterEach(() => {
      document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    });

    it("sends cookies and never an Authorization header, even if a token sits in localStorage", async () => {
      localStorage.setItem("access_token", "must-be-ignored");
      mockAdapter.mockResolvedValue({ data: "ok" });

      await api.get("/test");

      const config = mockAdapter.mock.calls[0][0];
      expect(config.withCredentials).toBe(true);
      expect(config.headers.Authorization).toBeUndefined();
    });

    it("adds the CSRF header on writes but not on reads", async () => {
      setCsrfCookie("csrf-abc123");
      mockAdapter.mockResolvedValue({ data: "ok" });

      await api.post("/things/", { a: 1 });
      await api.get("/things/");

      expect(mockAdapter.mock.calls[0][0].headers["X-CSRFToken"]).toBe("csrf-abc123");
      expect(mockAdapter.mock.calls[1][0].headers["X-CSRFToken"]).toBeUndefined();
    });

    it("refreshes through the cookie and retries the original request on 401", async () => {
      mockAdapter.mockRejectedValueOnce({
        response: { status: 401 },
        config: { url: "/protected", headers: {} },
      });
      mockAdapter.mockResolvedValueOnce({ data: "success" });
      axios.post.mockResolvedValue({ data: { detail: "refreshed" } });

      const response = await api.get("/protected");

      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(axios.post).toHaveBeenCalledWith(
        "/api/v1/token/refresh/",
        null,
        expect.objectContaining({ withCredentials: true })
      );
      expect(mockAdapter).toHaveBeenCalledTimes(2);
      expect(response.data).toBe("success");
      expect(localStorage.getItem("access_token")).toBeNull();
      expect(localStorage.getItem("refresh_token")).toBeNull();
    });

    it("redirects to /login when the refresh fails", async () => {
      const refreshError = new Error("Refresh failed");
      axios.post.mockRejectedValue(refreshError);
      mockAdapter.mockRejectedValue({
        response: { status: 401 },
        config: { url: "/protected" },
      });

      await expect(api.get("/protected")).rejects.toThrow(refreshError);

      expect(window.location.href).toBe("/login");
    });

    it("lets session probes handle a failed refresh themselves (skipAuthRedirect)", async () => {
      axios.post.mockRejectedValue(new Error("Refresh failed"));
      mockAdapter.mockRejectedValue({
        response: { status: 401 },
        config: { url: "/auth/me/", skipAuthRedirect: true },
      });

      await expect(api.get("/auth/me/", { skipAuthRedirect: true })).rejects.toBeDefined();

      expect(window.location.href).toBe("");
      expect(toast.error).not.toHaveBeenCalled();
    });

    it("shares a single refresh between concurrent 401s", async () => {
      mockAdapter.mockRejectedValueOnce({
        response: { status: 401 },
        config: { headers: {}, url: "/protected1" },
      });
      mockAdapter.mockRejectedValueOnce({
        response: { status: 401 },
        config: { headers: {}, url: "/protected2" },
      });
      mockAdapter.mockResolvedValue({ data: "success" });
      axios.post.mockResolvedValue({ data: { detail: "refreshed" } });

      const [response1, response2] = await Promise.all([api.get("/protected1"), api.get("/protected2")]);

      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(response1.data).toBe("success");
      expect(response2.data).toBe("success");
    });

    it("does not redirect public viewers when the refresh fails; it shows the error instead", async () => {
      window.location.pathname = "/view/public-slug";
      axios.post.mockRejectedValue(new Error("Refresh failed"));
      mockAdapter.mockRejectedValue({
        response: { status: 401, data: { detail: "Unauthorized link" } },
        config: { url: "/links/public-slug/data/" },
      });

      await expect(api.get("/links/public-slug/data/")).rejects.toBeDefined();

      expect(window.location.href).toBe("");
      expect(toast.error).toHaveBeenCalled();
    });
  });

  describe("Share Link View Data Params", () => {
    it("should include limit and offset when provided", async () => {
      mockAdapter.mockResolvedValue({ data: { ok: true } });

      await getShareLinkViewData("slug-1", {
        parentId: "folder-1",
        limit: DATAROOM_VIEWER_PAGE_SIZE,
        offset: DATAROOM_VIEWER_PAGE_SIZE * 2,
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-1/view-data/");
      expect(requestConfig.params).toEqual({
        parent_id: "folder-1",
        limit: DATAROOM_VIEWER_PAGE_SIZE,
        offset: DATAROOM_VIEWER_PAGE_SIZE * 2,
      });
    });

    it("should omit limit and offset when not provided", async () => {
      mockAdapter.mockResolvedValue({ data: { ok: true } });

      await getShareLinkViewData("slug-2", {
        parentId: "folder-2",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-2/view-data/");
      expect(requestConfig.params).toEqual({
        parent_id: "folder-2",
      });
    });

    it("should include viewSessionId parameter when provided", async () => {
      mockAdapter.mockResolvedValue({ data: { ok: true } });

      await getShareLinkViewData("slug-3", {
        dataroomDocumentId: "doc-1",
        viewSessionId: "session-123",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-3/view-data/");
      expect(requestConfig.params).toEqual({
        dataroom_document_id: "doc-1",
        view_session_id: "session-123",
      });
    });
  });

  describe("Share Link Q&A Params", () => {
    it("should include context when fetching public Q&A threads", async () => {
      mockAdapter.mockResolvedValue({ data: [] });

      await getPublicQnaThreads("slug-1", {
        viewSessionId: "view-1",
        dataroomDocumentId: "ddoc-1",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-1/qna-threads/");
      expect(requestConfig.params).toEqual({
        view_session_id: "view-1",
        dataroom_document_id: "ddoc-1",
      });
    });

    it("should include context when fetching public Q&A summary", async () => {
      mockAdapter.mockResolvedValue({ data: { thread_count: 2 } });

      await getPublicQnaSummary("slug-1", {
        viewSessionId: "view-1",
        dataroomFolderId: "folder-1",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-1/qna-summary/");
      expect(requestConfig.params).toEqual({
        view_session_id: "view-1",
        dataroom_folder_id: "folder-1",
      });
    });

    it("should send context when creating public Q&A threads", async () => {
      mockAdapter.mockResolvedValue({ data: { id: "thread-1" } });

      await createPublicQnaThread("slug-1", {
        viewSessionId: "view-1",
        subject: "Subject",
        body: "Body",
        dataroomFolderId: "folder-1",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-1/qna-threads/");
      expect(JSON.parse(requestConfig.data)).toEqual({
        view_session_id: "view-1",
        subject: "Subject",
        body: "Body",
        dataroom_folder_id: "folder-1",
      });
    });

    it("should include view session when fetching public Q&A messages", async () => {
      mockAdapter.mockResolvedValue({ data: [] });

      await getPublicQnaMessages("slug-1", "thread-1", { viewSessionId: "view-1" });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-1/qna-threads/thread-1/messages/");
      expect(requestConfig.params).toEqual({ view_session_id: "view-1" });
    });

    it("should post public Q&A replies", async () => {
      mockAdapter.mockResolvedValue({ data: { id: "msg-1" } });

      await createPublicQnaMessage("slug-1", "thread-1", {
        viewSessionId: "view-1",
        body: "Reply",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/links/slug-1/qna-threads/thread-1/messages/");
      expect(JSON.parse(requestConfig.data)).toEqual({
        view_session_id: "view-1",
        body: "Reply",
      });
    });
  });

  describe("Owner Q&A Params", () => {
    it("should fetch owner Q&A threads with document and status filters", async () => {
      mockAdapter.mockResolvedValue({ data: [] });

      await getOwnerQnaThreads({ documentId: "doc-1", status: "open" });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/qna-threads/");
      expect(requestConfig.params).toEqual({
        document_id: "doc-1",
        status: "open",
      });
    });

    it("should patch owner Q&A thread status", async () => {
      mockAdapter.mockResolvedValue({ data: { id: "thread-1", status: "closed" } });

      await updateOwnerQnaThreadStatus("thread-1", "closed");

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/qna-threads/thread-1/");
      expect(JSON.parse(requestConfig.data)).toEqual({ status: "closed" });
    });

    it("should post owner Q&A replies", async () => {
      mockAdapter.mockResolvedValue({ data: { id: "msg-1" } });

      await createOwnerQnaMessage("thread-1", "Owner reply");

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/qna-threads/thread-1/messages/");
      expect(JSON.parse(requestConfig.data)).toEqual({ body: "Owner reply" });
    });

    it("should post owner-created Q&A threads", async () => {
      mockAdapter.mockResolvedValue({ data: { id: "thread-1" } });

      await createOwnerQnaThread({
        shareLinkId: "link-1",
        subject: "Owner question",
        body: "Please review this.",
      });

      const requestConfig = mockAdapter.mock.calls[0][0];
      expect(requestConfig.url).toBe("/qna-threads/");
      expect(JSON.parse(requestConfig.data)).toEqual({
        share_link_id: "link-1",
        subject: "Owner question",
        body: "Please review this.",
      });
    });
  });

  describe("Page image requests", () => {
    it("fetches share-link pages with a script-only POST (no URL, no error toast)", async () => {
      const blob = new Blob(["x"]);
      mockAdapter.mockResolvedValue({ data: blob });

      const result = await fetchSharePageBlob("slug-1", 3, { dataroomDocumentId: "dd1" });

      const config = mockAdapter.mock.calls[0][0];
      expect(config.method).toBe("post");
      expect(config.url).toBe("/links/slug-1/pages/3/");
      expect(config.params).toEqual({ dataroom_document_id: "dd1" });
      expect(config.headers["X-Page-Request"]).toBe("1");
      expect(config.responseType).toBe("blob");
      expect(config.skipErrorToast).toBe(true);
      expect(result).toBe(blob);
    });

    it("omits the dataroom parameter for plain document links", async () => {
      mockAdapter.mockResolvedValue({ data: new Blob(["x"]) });

      await fetchSharePageBlob("slug-2", 1);

      expect(mockAdapter.mock.calls[0][0].params).toBeUndefined();
    });

    it("fetches owner document pages the same way", async () => {
      mockAdapter.mockResolvedValue({ data: new Blob(["x"]) });

      await fetchDocumentPageBlob("doc-9", 2);

      const config = mockAdapter.mock.calls[0][0];
      expect(config.method).toBe("post");
      expect(config.url).toBe("/documents/doc-9/pages/2/");
      expect(config.headers["X-Page-Request"]).toBe("1");
      expect(config.responseType).toBe("blob");
    });

    it("reports page failures inline instead of toasting", async () => {
      mockAdapter.mockRejectedValue({
        response: { status: 503, data: { detail: "Could not retrieve file." } },
        config: { url: "/links/s/pages/1/", skipErrorToast: true },
      });

      await expect(fetchSharePageBlob("s", 1)).rejects.toBeDefined();

      expect(toast.error).not.toHaveBeenCalled();
    });
  });

  describe("Dataroom Archive and Folder Downloads", () => {
    it("should trigger native anchor download for dataroom archive with viewSessionId", () => {
      const appendChildSpy = vi.spyOn(document.body, "appendChild");
      const clickSpy = vi.fn();

      const originalCreateElement = document.createElement.bind(document);
      vi.spyOn(document, "createElement").mockImplementation((tag) => {
        const el = originalCreateElement(tag);
        if (tag === "a") {
          el.click = clickSpy;
        }
        return el;
      });

      downloadDataroomArchive("my-slug", "view-123");

      expect(clickSpy).toHaveBeenCalled();
      const createdAnchor = appendChildSpy.mock.calls.find((call) => call[0].tagName === "A")?.[0];
      expect(createdAnchor).toBeDefined();
      expect(createdAnchor.href).toContain("/api/v1/links/my-slug/download-dataroom/?view_session_id=view-123");
      expect(createdAnchor.getAttribute("download")).toBe("");

      vi.restoreAllMocks();
    });

    it("should trigger native anchor download for dataroom folder with viewSessionId", () => {
      const appendChildSpy = vi.spyOn(document.body, "appendChild");
      const clickSpy = vi.fn();

      const originalCreateElement = document.createElement.bind(document);
      vi.spyOn(document, "createElement").mockImplementation((tag) => {
        const el = originalCreateElement(tag);
        if (tag === "a") {
          el.click = clickSpy;
        }
        return el;
      });

      downloadDataroomFolder("my-slug", "folder-456", "view-123");

      expect(clickSpy).toHaveBeenCalled();
      const createdAnchor = appendChildSpy.mock.calls.find((call) => call[0].tagName === "A")?.[0];
      expect(createdAnchor).toBeDefined();
      expect(createdAnchor.href).toContain("/api/v1/links/my-slug/download-folder/folder-456/?view_session_id=view-123");
      expect(createdAnchor.getAttribute("download")).toBe("");

      vi.restoreAllMocks();
    });
  });
});
