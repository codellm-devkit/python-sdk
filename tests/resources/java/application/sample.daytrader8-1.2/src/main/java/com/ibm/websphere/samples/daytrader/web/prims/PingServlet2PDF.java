/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.IOException;
import java.net.URL;
import java.net.URLConnection;

import javax.servlet.ServletException;
import javax.servlet.ServletOutputStream;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingServlet2PDF tests a call to a servlet which then loads a PDF document.
 *
 */
@WebServlet(name = "PingServlet2PDF", urlPatterns = { "/servlet/PingServlet2PDF" })
public class PingServlet2PDF extends HttpServlet {

    private static final long serialVersionUID = -1321793174442755868L;
    private static int hitCount = 0;
    private static final int BUFFER_SIZE = 1024 * 8; // 8 KB

    /**
     * forwards post requests to the doGet method Creation date: (11/6/2000
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        PingBean ab;
        BufferedInputStream bis = null;
        BufferedOutputStream bos = null;
        try {
            ab = new PingBean();
            hitCount++;
            ab.setMsg("Hit Count: " + hitCount);
            req.setAttribute("ab", ab);

            ServletOutputStream out = res.getOutputStream();

            // MIME type for pdf doc
            res.setContentType("application/pdf");

            // Open an InputStream to the PDF document
            String fileURL = "http://localhost:9080/daytrader/WAS_V7_64-bit_performance.pdf";
            URL url = new URL(fileURL);
            URLConnection conn = url.openConnection();
            bis = new BufferedInputStream(conn.getInputStream());

            // Transfer the InputStream (PDF Document) to OutputStream (servlet)
            bos = new BufferedOutputStream(out);
            byte[] buff = new byte[BUFFER_SIZE];
            int bytesRead;
            // Simple read/write loop.
            while (-1 != (bytesRead = bis.read(buff, 0, buff.length))) {
                bos.write(buff, 0, bytesRead);
            }

        } catch (Exception ex) {
            Log.error(ex, "PingServlet2Jsp.doGet(...): request error");
            res.sendError(500, "PingServlet2Jsp.doGet(...): request error" + ex.toString());

        }

        finally {
            if (bis != null) {
                bis.close();
            }
            if (bos != null) {
                bos.close();
            }
        }

    }
}