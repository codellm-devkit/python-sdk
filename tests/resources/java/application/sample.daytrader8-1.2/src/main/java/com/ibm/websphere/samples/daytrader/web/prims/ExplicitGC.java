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

import java.io.IOException;

import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletOutputStream;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * ExplicitGC invokes System.gc(). This allows one to gather min / max heap
 * statistics.
 *
 */
@WebServlet(name = "ExplicitGC", urlPatterns = { "/servlet/ExplicitGC" })
public class ExplicitGC extends HttpServlet {

    private static final long serialVersionUID = -3758934393801102408L;
    private static String initTime;
    private static int hitCount;

    /**
     * forwards post requests to the doGet method Creation date: (01/29/2006
     * 20:10:00 PM)
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
        try {
            res.setContentType("text/html");

            ServletOutputStream out = res.getOutputStream();
            hitCount++;
            long totalMemory = Runtime.getRuntime().totalMemory();

            long maxMemoryBeforeGC = Runtime.getRuntime().maxMemory();
            long freeMemoryBeforeGC = Runtime.getRuntime().freeMemory();
            long startTime = System.currentTimeMillis();

            System.gc(); // Invoke the GC.

            long endTime = System.currentTimeMillis();
            long maxMemoryAfterGC = Runtime.getRuntime().maxMemory();
            long freeMemoryAfterGC = Runtime.getRuntime().freeMemory();

            out.println("<html><head><title>ExplicitGC</title></head>"
                    + "<body><HR><BR><FONT size=\"+2\" color=\"#000066\">Explicit Garbage Collection<BR></FONT><FONT size=\"+1\" color=\"#000066\">Init time : "
                    + initTime
                    + "<BR><BR></FONT>  <B>Hit Count: "
                    + hitCount
                    + "<br>"
                    + "<table border=\"0\"><tr>"
                    + "<td align=\"right\">Total Memory</td><td align=\"right\">"
                    + totalMemory
                    + "</td>"
                    + "</tr></table>"
                    + "<table width=\"350\"><tr><td colspan=\"2\" align=\"left\">"
                    + "Statistics before GC</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Max Memory</td><td align=\"right\">"
                    + maxMemoryBeforeGC
                    + "</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Free Memory</td><td align=\"right\">"
                    + freeMemoryBeforeGC
                    + "</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Used Memory</td><td align=\"right\">"
                    + (totalMemory - freeMemoryBeforeGC)
                    + "</td></tr>"
                    + "<tr><td colspan=\"2\" align=\"left\">Statistics after GC</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Max Memory</td><td align=\"right\">"
                    + maxMemoryAfterGC
                    + "</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Free Memory</td><td align=\"right\">"
                    + freeMemoryAfterGC
                    + "</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Used Memory</td><td align=\"right\">"
                    + (totalMemory - freeMemoryAfterGC)
                    + "</td></tr>"
                    + "<tr><td align=\"right\">"
                    + "Total Time in GC</td><td align=\"right\">"
                    + Float.toString((endTime - startTime) / 1000)
                    + "s</td></tr>"
                    + "</table>" + "</body></html>");
        } catch (Exception e) {
            Log.error(e, "ExplicitGC.doGet(...): general exception caught");
            res.sendError(500, e.toString());

        }
    }

    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/
    @Override
    public String getServletInfo() {
        return "Generate Explicit GC to VM";
    }

    /**
     * called when the class is loaded to initialize the servlet
     *
     * @param config
     *            ServletConfig:
     **/
    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        initTime = new java.util.Date().toString();
        hitCount = 0;

    }
}
