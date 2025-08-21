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

import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingServlet2JSP tests a call from a servlet to a JavaServer Page providing
 * server-side dynamic HTML through JSP scripting.
 *
 */
@WebServlet(name = "PingServlet2Jsp", urlPatterns = { "/servlet/PingServlet2Jsp" })
public class PingServlet2Jsp extends HttpServlet {
    private static final long serialVersionUID = -5199543766883932389L;
    private static int hitCount = 0;

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
        try {
            ab = new PingBean();
            hitCount++;
            ab.setMsg("Hit Count: " + hitCount);
            req.setAttribute("ab", ab);

            getServletConfig().getServletContext().getRequestDispatcher("/PingServlet2Jsp.jsp").forward(req, res);
        } catch (Exception ex) {
            Log.error(ex, "PingServlet2Jsp.doGet(...): request error");
            res.sendError(500, "PingServlet2Jsp.doGet(...): request error" + ex.toString());

        }
    }
}